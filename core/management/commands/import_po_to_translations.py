"""Seed the Translation table from on-disk .po files.

Run once after the 0006_translation migration lands to bootstrap the DB
from whatever was committed in the images' .po files.

The DB is the durable source of truth for translations — Rosetta saves
upsert Translation rows directly, so the default behaviour here is
**additive only**: rows that already exist in the DB are preserved and
only missing msgids are seeded. Pass `--force` to restore the historical
destructive behaviour (overwrite every row from .po, wiping any Rosetta
edits that were not committed back to .po). Empty msgstrs are always
skipped.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import polib
from django.conf import settings
from django.core.cache import cache
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Translation
from core.rosetta_storage import TRANSLATION_VERSION_CACHE_KEY

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Import msgstr values from every LOCALE_PATHS/<lang>/LC_MESSAGES/"
        "django.po file into the Translation table."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--language",
            "-l",
            dest="languages",
            action="append",
            default=None,
            help=(
                "Limit import to the given language codes (repeat flag). "
                "Defaults to every locale discovered under LOCALE_PATHS."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse .po files and report counts without writing to the DB.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help=(
                "Overwrite existing Translation rows from .po. Without this "
                "flag the command is additive-only: rows already in the DB "
                "(i.e. every Rosetta edit) are preserved."
            ),
        )

    def handle(self, *args, **options):
        language_filter = options.get("languages")
        dry_run = options.get("dry_run", False)
        force = options.get("force", False)

        if force:
            self.stdout.write(
                self.style.WARNING(
                    "--force set: existing Translation rows will be "
                    "overwritten from .po. Any Rosetta edit that isn't in "
                    "the committed .po file will be lost."
                )
            )

        if not settings.LOCALE_PATHS:
            raise CommandError("LOCALE_PATHS is empty.")

        total_imported = 0
        total_skipped = 0
        total_preserved = 0

        for locale_root in settings.LOCALE_PATHS:
            root = Path(locale_root)
            if not root.is_dir():
                self.stdout.write(
                    self.style.WARNING(
                        f"Skipping missing LOCALE_PATHS entry: {root}"
                    )
                )
                continue

            for lang_dir in sorted(root.iterdir()):
                if not lang_dir.is_dir():
                    continue
                if language_filter and lang_dir.name not in language_filter:
                    continue

                po_path = lang_dir / "LC_MESSAGES" / "django.po"
                if not po_path.exists():
                    continue

                imported, skipped, preserved = self._import_po(
                    lang_dir.name,
                    str(po_path),
                    dry_run=dry_run,
                    force=force,
                )
                total_imported += imported
                total_skipped += skipped
                total_preserved += preserved
                verb = "Would import" if dry_run else "Imported"
                self.stdout.write(
                    self.style.SUCCESS(
                        f"{verb} {imported} rows from {po_path} "
                        f"({skipped} skipped empty, {preserved} preserved)"
                    )
                )

        summary = (
            f"Total: {total_imported} rows imported, "
            f"{total_preserved} preserved (DB already had a row), "
            f"{total_skipped} skipped (empty msgstr)."
        )
        if dry_run:
            summary = f"[DRY RUN] {summary}"
        self.stdout.write(self.style.SUCCESS(summary))

        if not dry_run and total_imported:
            # Bump the shared version tick so every running pod's
            # TranslationReloadMiddleware picks up the freshly-seeded
            # DB state on its next request — no need to restart pods
            # after the first import or a manual reconciliation run.
            try:
                cache.set(
                    TRANSLATION_VERSION_CACHE_KEY, time.time(), timeout=None
                )
                self.stdout.write(
                    self.style.SUCCESS(
                        "Bumped translation version cache key; "
                        "running pods will re-overlay on next request."
                    )
                )
            except Exception as exc:  # pragma: no cover — defensive
                self.stdout.write(
                    self.style.WARNING(
                        f"Could not bump translation version key: {exc}"
                    )
                )

    def _import_po(
        self, language_code: str, po_path: str, *, dry_run: bool, force: bool
    ) -> tuple[int, int, int]:
        po = polib.pofile(po_path)
        candidates: list[Translation] = []
        skipped = 0

        for entry in po:
            if entry.obsolete:
                continue

            if entry.msgid_plural:
                for plural_index, msgstr in (entry.msgstr_plural or {}).items():
                    if not msgstr:
                        skipped += 1
                        continue
                    candidates.append(
                        Translation(
                            language_code=language_code,
                            msgid=entry.msgid or "",
                            msgid_plural=entry.msgid_plural,
                            plural_index=int(plural_index),
                            msgstr=msgstr,
                        )
                    )
            else:
                if not entry.msgstr:
                    skipped += 1
                    continue
                candidates.append(
                    Translation(
                        language_code=language_code,
                        msgid=entry.msgid or "",
                        msgid_plural="",
                        plural_index=0,
                        msgstr=entry.msgstr,
                    )
                )

        if not candidates:
            return 0, skipped, 0

        # Additive mode: load every existing (msgid, plural_index) key for
        # this language in one query so we can skip rows already in the DB
        # — those came from a Rosetta edit and are the operator's intent.
        preserved = 0
        if not force:
            existing_keys = set(
                Translation.objects.filter(
                    language_code=language_code
                ).values_list("msgid", "plural_index")
            )
            filtered: list[Translation] = []
            for row in candidates:
                if (row.msgid, row.plural_index) in existing_keys:
                    preserved += 1
                    continue
                filtered.append(row)
            candidates = filtered

        imported = len(candidates)

        if dry_run or not candidates:
            return imported, skipped, preserved

        with transaction.atomic():
            for row in candidates:
                Translation.objects.update_or_create(
                    language_code=row.language_code,
                    msgid=row.msgid,
                    plural_index=row.plural_index,
                    defaults={
                        "msgid_plural": row.msgid_plural,
                        "msgstr": row.msgstr,
                    },
                )
        return imported, skipped, preserved
