"""Audit the DB translation overlay against the compiled .po catalogue.

Every non-empty `Translation` row is overlaid onto the in-memory
gettext catalog of every pod and worker (core/rosetta_storage.py), so a
row that disagrees with django.po silently wins over it — across every
deploy, in every process, with nothing on disk to show for it. That is
exactly right for a deliberate Rosetta edit and a disaster for a stale
bulk import: on 2026-09-12 an April row 'Order Received' → 'Η
Παραγγελία Παραδόθηκε' retitled the order-confirmation email as
"delivered" (order #281), and a fresh `manage.py shell` — which never
applies the overlay — kept insisting the catalogue was fine.

Each row is classified against the on-disk catalogue:

  custom     msgstr differs from the .po value — the row is doing work.
             Listed in full. Whether it is an edit to keep or a stale
             import to drop is a human call, so it is never deleted
             without being named (`--drop-ids` / `--drop-before`).
  redundant  msgstr equals the .po value — the row changes nothing.
             `--prune` deletes these.
  unmatched  the compiled catalogue has nothing for the key (the msgid
             is gone from the .po, or untranslated there). Left alone.

Every write bumps the translation version key so each pod and worker
rebuilds its catalog from disk on its next request or task.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, datetime

from django.core.cache import cache
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.translation import trans_real

from core.models import Translation
from core.rosetta_storage import (
    TRANSLATION_VERSION_CACHE_KEY,
    _configured_languages,
    _reload_translations,
    overlay_key,
)


@dataclass
class LanguageAudit:
    language: str
    custom: list[tuple[Translation, str]] = field(default_factory=list)
    redundant: list[Translation] = field(default_factory=list)
    unmatched: list[Translation] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.custom) + len(self.redundant) + len(self.unmatched)


def _disk_catalog(language: str):
    """The catalogue as compiled from disk, with no overlay applied.

    `_reload_translations` evicts whatever this process has built so
    far — including an overlaid catalog — so the rebuild that follows
    is pure .mo content.
    """
    _reload_translations()
    return trans_real.translation(language)._catalog


def audit_language(language: str) -> LanguageAudit:
    catalog = _disk_catalog(language)
    result = LanguageAudit(language)
    rows = Translation.objects.filter(language_code=language).exclude(msgstr="")
    for row in rows.order_by("updated_at", "id"):
        po = catalog.get(
            overlay_key(row.msgid, row.msgid_plural, row.plural_index)
        )
        if po is None:
            result.unmatched.append(row)
        elif po == row.msgstr:
            result.redundant.append(row)
        else:
            result.custom.append((row, po))
    return result


def _cut(text: str, width: int = 70) -> str:
    text = text.replace("\n", " ")
    return text if len(text) <= width else text[: width - 1] + "…"


class Command(BaseCommand):
    help = (
        "Compare every Translation overlay row with the compiled .po "
        "catalogue; list the rows that override it, prune the ones that "
        "change nothing."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--language",
            "-l",
            dest="languages",
            action="append",
            default=None,
            help="Limit to the given language codes (repeat flag).",
        )
        parser.add_argument(
            "--prune",
            action="store_true",
            help="Delete the rows whose msgstr equals the .po value.",
        )
        parser.add_argument(
            "--drop-ids",
            nargs="+",
            type=int,
            default=None,
            metavar="ID",
            help="Delete these rows by id (any class).",
        )
        parser.add_argument(
            "--drop-before",
            type=date.fromisoformat,
            default=None,
            metavar="YYYY-MM-DD",
            help=(
                "Delete every row last updated before this date — for a "
                "bulk import the .po has since superseded."
            ),
        )

    def handle(self, *args, **options):
        languages = options["languages"] or _configured_languages()
        unknown = set(languages) - set(_configured_languages())
        if unknown:
            raise CommandError(
                f"Unknown language(s): {', '.join(sorted(unknown))}"
            )

        for language in languages:
            self._report(audit_language(language))

        doomed = Translation.objects.none()
        if options["prune"]:
            redundant_ids = [
                row.id
                for language in languages
                for row in audit_language(language).redundant
            ]
            doomed |= Translation.objects.filter(id__in=redundant_ids)
        if options["drop_ids"]:
            doomed |= Translation.objects.filter(id__in=options["drop_ids"])
        if options["drop_before"]:
            cutoff = timezone.make_aware(
                datetime.combine(options["drop_before"], datetime.min.time())
            )
            doomed |= Translation.objects.filter(
                language_code__in=languages, updated_at__lt=cutoff
            )

        if not (
            options["prune"] or options["drop_ids"] or options["drop_before"]
        ):
            return

        deleted, _ = doomed.delete()
        cache.set(TRANSLATION_VERSION_CACHE_KEY, time.time(), timeout=None)
        self.stdout.write(
            self.style.SUCCESS(
                f"Deleted {deleted} overlay row(s); translation version "
                "bumped, every process reloads on its next request/task."
            )
        )

    def _report(self, audit: LanguageAudit) -> None:
        self.stdout.write(
            f"{audit.language}: {audit.total} rows — "
            f"{len(audit.custom)} custom, {len(audit.redundant)} redundant, "
            f"{len(audit.unmatched)} unmatched"
        )
        if not audit.custom:
            return
        self.stdout.write("  custom rows (msgstr overrides django.po):")
        for row, po in audit.custom:
            self.stdout.write(
                f"    #{row.id} {row.updated_at:%Y-%m-%d} {_cut(row.msgid)!r}"
            )
            self.stdout.write(f"        db: {_cut(row.msgstr)!r}")
            self.stdout.write(f"        po: {_cut(po)!r}")
