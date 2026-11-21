"""Record the displayed size of every stored image that has none yet.

``ImageAndSvgField`` measures an image when it is assigned (an upload, on
save); this command measures the rows stored before a model gained
``width_field``/``height_field``, and the rows a release that did not
measure yet wrote during a deploy, so run it once after the rollout of a
release that adds the fields. Safe to run again: a row with both sizes is
skipped, and only the header of each file is read
(``core.fields.image.displayed_dimensions``).

It writes the two columns directly rather than saving the row: a save
re-validates, records history and re-indexes for a change no reader of
those sees. It then queues the purge of the cache surfaces that declare a
model it changed (``invalidated_by``), as a save would, so the API serves
the sizes at once. An SVG has no raster size and stays null (it is read again on
the next run); a file missing from storage is reported and left alone.

Fields come from the app registry, so a model that gains the fields is
covered without touching this command.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.core.exceptions import SuspiciousFileOperation
from django.core.management.base import BaseCommand
from django.db import models

from core.cache.invalidation import mark_surface_stale
from core.cache.registry import iter_surfaces
from core.fields.image import ImageAndSvgField, displayed_dimensions
from core.management.commands.media_reference_audit import tenant_models
from core.management.tenant_mixin import TenantCommandMixin


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
    for model, field in pairs:
        tally = tallies.setdefault(f"{model._meta.label}.{field.name}", Tally())
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
            except FileNotFoundError, SuspiciousFileOperation:
                tally.missing += 1
                continue
            if size is None:
                tally.unmeasurable += 1
                continue
            tally.measured += 1
            if not dry_run:
                model._base_manager.filter(**{model._meta.pk.name: pk}).update(
                    **{
                        field.width_field: size[0],
                        field.height_field: size[1],
                    }
                )
    if not dry_run:
        _mark_surfaces_stale(
            {
                label.rsplit(".", 1)[0]
                for label, tally in tallies.items()
                if tally.measured
            }
        )
    return tallies


def _mark_surfaces_stale(model_labels: set[str]) -> None:
    """Queue the purge of every cache surface that renders one of
    *model_labels* (``app.Model``)."""
    for surface in iter_surfaces():
        if model_labels & set(surface.invalidated_by):
            mark_surface_stale(surface.code)


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
                        f"{tally.missing} missing from storage"
                    )
        verb = "would measure" if options["dry_run"] else "measured"
        self.stdout.write(self.style.SUCCESS(f"{verb} {measured} row(s)."))
        if missing:
            self.stdout.write(
                self.style.WARNING(
                    f"{missing} row(s) name a file that is not in storage."
                )
            )
