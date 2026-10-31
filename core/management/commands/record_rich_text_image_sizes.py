"""Record the real size of every uploaded image in stored rich text.

``RichTextField`` does this on every save
(``core.utils.rich_text_images``); this command brings the rows saved
before that existed up to date. Safe to run again: a row whose images
already carry the right size is left alone.

It writes the column directly rather than saving the row. A save
re-validates the whole body and would refuse one the policy has since
tightened over, and it re-indexes and records history for a change that
touches no text. The search index holds a rich-text field's visible text
only (``core.utils.html_text``), never its attributes, so the sizes
cannot change a document and nothing needs reindexing after this run.
The storefront keeps serving a cached page until it expires, so run
``purge_edge_cache`` afterwards to have the sizes used at once.

Fields come from the app registry, so a model that gains a rich-text
column is covered without touching this command.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.core.management.base import BaseCommand
from django.db import models

from core.fields.rich_text import RichTextField
from core.management.commands.media_reference_audit import tenant_models
from core.management.tenant_mixin import TenantCommandMixin
from core.utils.rich_text_images import record_natural_sizes


@dataclass
class Tally:
    scanned: int = 0
    changed: int = 0


def rich_text_fields() -> list[tuple[type[models.Model], list]]:
    """Every concrete TENANT_APPS model with a ``RichTextField``."""
    pairs = []
    for model in tenant_models():
        fields = [
            f for f in model._meta.get_fields() if isinstance(f, RichTextField)
        ]
        if fields:
            pairs.append((model, fields))
    return pairs


def record_current_schema(pairs, *, dry_run: bool = False) -> dict[str, Tally]:
    """Update every rich-text column in the connected schema.

    ``_base_manager`` rather than the default manager: a soft-deleted
    row is hidden by the default one and can still be restored.
    """
    tallies: dict[str, Tally] = {}
    for model, fields in pairs:
        for field in fields:
            tally = tallies.setdefault(
                f"{model._meta.label}.{field.name}", Tally()
            )
            rows = model._base_manager.order_by().values_list(
                model._meta.pk.name, field.name
            )
            for pk, body in rows.iterator(chunk_size=200):
                tally.scanned += 1
                if not body:
                    continue
                updated = record_natural_sizes(body)
                if updated == body:
                    continue
                tally.changed += 1
                if not dry_run:
                    model._base_manager.filter(
                        **{model._meta.pk.name: pk}
                    ).update(**{field.name: updated})
    return tallies


class Command(TenantCommandMixin, BaseCommand):
    help = "Record the real size of uploaded images in stored rich text."
    require_tenant_scope = True

    def add_arguments(self, parser):
        self.add_tenant_arguments(parser)
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change and write nothing.",
        )

    def handle(self, *args, **options):
        pairs = rich_text_fields()
        total = 0
        for context in self.iter_tenant_contexts(options):
            with context:
                tallies = record_current_schema(
                    pairs, dry_run=options["dry_run"]
                )
            for label, tally in tallies.items():
                total += tally.changed
                if tally.scanned:
                    self.stdout.write(
                        f"    {label}: {tally.changed} of "
                        f"{tally.scanned} row(s) changed"
                    )
        verb = "would change" if options["dry_run"] else "changed"
        self.stdout.write(self.style.SUCCESS(f"{verb} {total} row(s)."))
