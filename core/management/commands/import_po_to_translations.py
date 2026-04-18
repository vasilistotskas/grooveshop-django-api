"""Seed the Translation table from on-disk .po files.

Run once after the 0006_translation migration lands, or whenever you
want to force-overwrite DB msgstrs from whatever is on disk (e.g.
after a manual .po merge).

Idempotent — msgstrs already in the DB get overwritten. Empty msgstrs
are skipped to keep the table tidy.
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

    def handle(self, *args, **options):
        language_filter = options.get("languages")
        dry_run = options.get("dry_run", False)

        if not settings.LOCALE_PATHS:
            raise CommandError("LOCALE_PATHS is empty.")

        total_imported = 0
        total_skipped = 0

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

                imported, skipped = self._import_po(
                    lang_dir.name, str(po_path), dry_run=dry_run
                )
                total_imported += imported
                total_skipped += skipped
                verb = "Would import" if dry_run else "Imported"
                self.stdout.write(
                    self.style.SUCCESS(
                        f"{verb} {imported} rows from {po_path} "
                        f"({skipped} skipped)"
                    )
                )

        summary = (
            f"Total: {total_imported} rows imported, "
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
        self, language_code: str, po_path: str, *, dry_run: bool
    ) -> tuple[int, int]:
        po = polib.pofile(po_path)
        imported = 0
        skipped = 0
        rows_to_write: list[Translation] = []

        for entry in po:
            if entry.obsolete:
                continue

            if entry.msgid_plural:
                for plural_index, msgstr in (entry.msgstr_plural or {}).items():
                    if not msgstr:
                        skipped += 1
                        continue
                    rows_to_write.append(
                        Translation(
                            language_code=language_code,
                            msgid=entry.msgid or "",
                            msgid_plural=entry.msgid_plural,
                            plural_index=int(plural_index),
                            msgstr=msgstr,
                        )
                    )
                    imported += 1
            else:
                if not entry.msgstr:
                    skipped += 1
                    continue
                rows_to_write.append(
                    Translation(
                        language_code=language_code,
                        msgid=entry.msgid or "",
                        msgid_plural="",
                        plural_index=0,
                        msgstr=entry.msgstr,
                    )
                )
                imported += 1

        if dry_run or not rows_to_write:
            return imported, skipped

        with transaction.atomic():
            for row in rows_to_write:
                Translation.objects.update_or_create(
                    language_code=row.language_code,
                    msgid=row.msgid,
                    plural_index=row.plural_index,
                    defaults={
                        "msgid_plural": row.msgid_plural,
                        "msgstr": row.msgstr,
                    },
                )
        return imported, skipped
