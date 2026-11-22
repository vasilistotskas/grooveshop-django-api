"""Record the displayed size of every stored image that has none yet.

``ImageAndSvgField`` measures an image when it is assigned (an upload, on
save); this command measures the rows stored before a model gained
``width_field``/``height_field``, and the rows a release that did not
measure yet wrote during a deploy, so run it once after the rollout of a
release that adds the fields. Safe to run again: a row with both sizes is
skipped, and only the header of each file is read
(``core.utils.image_size.displayed_dimensions``).

It writes the two columns directly rather than saving the row: a save
re-validates, records history and re-indexes for a change no reader of
those sees. It then queues the purge of the cache surfaces that declare a
model it changed (``invalidated_by``), as a save would, so the API serves
the sizes at once. An SVG has no raster size and stays null (it is read again on
the next run); a file missing from storage, or one it cannot read, is
reported and left alone.

Fields come from the app registry, so a model that gains the fields is
covered without touching this command.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.core.exceptions import SuspiciousFileOperation
from django.core.management.base import BaseCommand
from django.db import models

from core.cache.invalidation import mark_surface_stale, surfaces_invalidated_by
from core.fields.image import ImageAndSvgField
from core.management.commands.media_reference_audit import tenant_models
from core.management.tenant_mixin import TenantCommandMixin
from core.utils.image_size import displayed_dimensions


@dataclass
class Tally:
    scanned: int = 0
    measured: int = 0
    unmeasurable: int = 0
    missing: int = 0


def measured_fields() -> list[tuple[type[models.Model], ImageAndSvgField]]:
    """Every TENANT_APPS image field that records its size."""
    return [
        (model, field)
        for model in tenant_models()
        for field in model._meta.get_fields()
        if isinstance(field, ImageAndSvgField)
        and field.width_field
        and field.height_field
    ]


def backfill_current_schema(
    pairs: list[tuple[type[models.Model], ImageAndSvgField]],
    *,
    dry_run: bool = False,
) -> dict[str, Tally]:
    """Measure every unmeasured stored image in the connected schema.

    ``_base_manager``: a soft-deleted row is hidden by the default one and
    can still be restored.
    """
    tallies: dict[str, Tally] = {}
    changed: set[type[models.Model]] = set()
    try:
        for model, field in pairs:
            tally = tallies.setdefault(
                f"{model._meta.label}.{field.name}", Tally()
            )
            _measure_rows(model, field, tally, changed, dry_run=dry_run)
    finally:
        # Also when the run stops early: a row it wrote is skipped by the
        # next run, which would then purge nothing for it.
        for code in {
            code for model in changed for code in surfaces_invalidated_by(model)
        }:
            mark_surface_stale(code)
    return tallies


def _measure_rows(
    model: type[models.Model],
    field: ImageAndSvgField,
    tally: Tally,
    changed: set[type[models.Model]],
    *,
    dry_run: bool,
) -> None:
    """Measure *field*'s unmeasured rows of *model*, adding *model* to
    *changed* before its first write."""
    rows = (
        model._base_manager.order_by()
        .exclude(**{f"{field.attname}__isnull": True})
        .exclude(**{field.attname: ""})
        .filter(
            models.Q(**{f"{field.width_field}__isnull": True})
            | models.Q(**{f"{field.height_field}__isnull": True})
        )
        .values_list(model._meta.pk.name, field.attname)
    )
    for pk, name in rows.iterator(chunk_size=200):
        tally.scanned += 1
        try:
            with field.storage.open(name) as stored:
                size = displayed_dimensions(stored)
        except OSError, SuspiciousFileOperation:
            # Missing, or not readable (a directory, no permission).
            tally.missing += 1
            continue
        if size is None:
            tally.unmeasurable += 1
            continue
        if dry_run:
            tally.measured += 1
            continue
        # Before the write, so a run stopped right after it still purges
        # (at worst one purge too many when the row was replaced).
        changed.add(model)
        # Only while the row still names the file measured: a save that
        # replaced it meanwhile measured its own file.
        if model._base_manager.filter(
            **{model._meta.pk.name: pk, field.attname: name}
        ).update(**{field.width_field: size[0], field.height_field: size[1]}):
            tally.measured += 1


class Command(TenantCommandMixin, BaseCommand):
    help = "Record the displayed size of stored images that have none."
    require_tenant_scope = True
    include_suspended_stores = True

    def add_arguments(self, parser):
        self.add_tenant_arguments(parser)
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be measured and write nothing.",
        )

    def handle(self, *args, **options):
        pairs = measured_fields()
        measured = 0
        missing = 0
        for context in self.iter_tenant_contexts(options):
            with context:
                tallies = backfill_current_schema(
                    pairs, dry_run=options["dry_run"]
                )
            for label, tally in tallies.items():
                measured += tally.measured
                missing += tally.missing
                if tally.scanned:
                    self.stdout.write(
                        f"    {label}: {tally.measured} of "
                        f"{tally.scanned} row(s) measured, "
                        f"{tally.unmeasurable} without a raster size, "
                        f"{tally.missing} missing from or unreadable in storage"
                    )
        verb = "would measure" if options["dry_run"] else "measured"
        self.stdout.write(self.style.SUCCESS(f"{verb} {measured} row(s)."))
        if missing:
            self.stdout.write(
                self.style.WARNING(
                    f"{missing} row(s) name a file that is not in storage or cannot be read."
                )
            )
