"""Management command: backfill_recommendation_slots

Seeds ``RecommendationSlot`` rows for every store, suspended ones included
(or one ``--schema``), from the tenant's own ``vertical`` preset, or from
``--vertical`` to override it for this run. New tenants get this at
provisioning; the PreSync job runs it on every deploy so tenants that
predate the engine — or a surface added in a later release — get their
rows without a manual step. A suspended store is seeded too: this brings
its data up to date with the code and starts no work, so a store
suspended through a few deploys has its rows when it is reactivated.

Usage:
    uv run python manage.py backfill_recommendation_slots
    uv run python manage.py backfill_recommendation_slots --schema webside
    uv run python manage.py backfill_recommendation_slots --schema webside --vertical fashion
    uv run python manage.py backfill_recommendation_slots --dry-run

Safe to re-run: seeding is ``get_or_create`` — it only creates missing
surfaces and never overwrites a slot the merchant has edited. To
re-apply a preset over edited rows use "Reset to preset" in the slot
admin, which is the explicit, per-store, per-slot way back.
"""

from __future__ import annotations

import logging

from django.core.management.base import BaseCommand, CommandError
from django_tenants.utils import schema_context

from core.management.tenant_mixin import TenantCommandMixin
from tenant.enum import StoreVertical

logger = logging.getLogger(__name__)


class Command(TenantCommandMixin, BaseCommand):
    help = (
        "Seed missing RecommendationSlot rows from each tenant's vertical "
        "preset into every store's schema, suspended ones included (or a "
        "single --schema). "
        "Idempotent — never overwrites an existing slot."
    )

    include_suspended_stores = True

    def add_arguments(self, parser):
        parser.add_argument(
            "--schema",
            default=None,
            help="Limit to one store's schema. Defaults to every store.",
        )
        parser.add_argument(
            "--vertical",
            default=None,
            choices=StoreVertical.values,
            help=(
                "Seed from this vertical's preset instead of each tenant's "
                "own ``vertical``. Affects only the rows created by this "
                "run; it does not change the tenant."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Print which tenants would be seeded without writing.",
        )

    def handle(self, *args, **options):
        target_schema = options["schema"]
        override = options["vertical"]
        dry_run = options["dry_run"]
        tenants = self.target_stores()
        if target_schema:
            tenants = tenants.filter(schema_name=target_schema)
            if not tenants.exists():
                raise CommandError(
                    f"No store with schema_name={target_schema!r}."
                )

        total = tenants.count()
        if total == 0:
            self.stdout.write(self.style.WARNING("No stores."))
            return

        prefix = "[DRY RUN] " if dry_run else ""
        source = (
            f"vertical {override!r}" if override else "each tenant's vertical"
        )
        self.stdout.write(
            f"{prefix}Seeding from {source} into {total} tenant(s)…"
        )

        ok = failed = created_total = 0
        for tenant in tenants:
            vertical = override or tenant.vertical
            if dry_run:
                self.stdout.write(
                    f"  WOULD seed schema={tenant.schema_name!r} "
                    f"vertical={vertical!r} (tenant: {tenant.name})"
                )
                ok += 1
                continue
            try:
                with schema_context(tenant.schema_name):
                    from recommendation.presets import (
                        seed_recommendation_slots,
                    )

                    created = seed_recommendation_slots(vertical)
                created_total += created
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  OK  schema={tenant.schema_name!r} "
                        f"vertical={vertical!r} created={created} "
                        f"(tenant: {tenant.name})"
                    )
                )
                ok += 1
            except Exception as exc:
                self.stderr.write(
                    self.style.ERROR(
                        f"  FAIL schema={tenant.schema_name!r} "
                        f"(tenant: {tenant.name}): {exc}"
                    )
                )
                logger.exception(
                    "backfill_recommendation_slots failed for schema=%s",
                    tenant.schema_name,
                )
                failed += 1

        style = self.style.SUCCESS if failed == 0 else self.style.WARNING
        self.stdout.write(
            style(
                f"\n{prefix}Done: {ok} succeeded, {failed} failed, "
                f"{created_total} slot(s) created."
            )
        )
