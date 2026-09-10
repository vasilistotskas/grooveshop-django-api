"""Management command: backfill_recommendation_slots

Seeds ``RecommendationSlot`` rows for every active non-public tenant
(or one ``--schema``) from a vertical preset. New tenants get this at
provisioning; this command is for tenants that predate the engine, and
for applying a vertical preset after onboarding.

Usage:
    uv run python manage.py backfill_recommendation_slots
    uv run python manage.py backfill_recommendation_slots --schema webside
    uv run python manage.py backfill_recommendation_slots --preset fashion --schema webside
    uv run python manage.py backfill_recommendation_slots --dry-run

Safe to re-run: ``seed_recommendation_slots`` is ``get_or_create`` —
it only creates missing surfaces and never overwrites a slot the
merchant has edited. To re-apply a preset over edited rows, delete the
rows in admin first; that is deliberately a two-step operation.
"""

from __future__ import annotations

import logging

from django.core.management.base import BaseCommand, CommandError
from django_tenants.utils import get_public_schema_name, schema_context

from recommendation.presets import DEFAULT_PRESET, PRESETS

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Seed missing RecommendationSlot rows from a vertical preset "
        "into all active tenant schemas (or a single --schema). "
        "Idempotent — never overwrites an existing slot."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--schema",
            default=None,
            help="Limit to one tenant schema. Defaults to all active tenants.",
        )
        parser.add_argument(
            "--preset",
            default=DEFAULT_PRESET,
            choices=sorted(PRESETS),
            help=f"Vertical preset to seed from (default: {DEFAULT_PRESET}).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Print which tenants would be seeded without writing.",
        )

    def handle(self, *args, **options):
        from tenant.models import Tenant

        target_schema = options["schema"]
        preset = options["preset"]
        dry_run = options["dry_run"]
        public_schema = get_public_schema_name()

        tenants = Tenant.objects.filter(is_active=True).exclude(
            schema_name=public_schema
        )
        if target_schema:
            tenants = tenants.filter(schema_name=target_schema)
            if not tenants.exists():
                raise CommandError(
                    f"No active non-public tenant with "
                    f"schema_name={target_schema!r}."
                )

        total = tenants.count()
        if total == 0:
            self.stdout.write(self.style.WARNING("No active tenants."))
            return

        prefix = "[DRY RUN] " if dry_run else ""
        self.stdout.write(
            f"{prefix}Seeding preset {preset!r} into {total} tenant(s)…"
        )

        ok = failed = created_total = 0
        for tenant in tenants:
            if dry_run:
                self.stdout.write(
                    f"  WOULD seed schema={tenant.schema_name!r} "
                    f"(tenant: {tenant.name})"
                )
                ok += 1
                continue
            try:
                with schema_context(tenant.schema_name):
                    from recommendation.presets import (
                        seed_recommendation_slots,
                    )

                    created = seed_recommendation_slots(preset)
                created_total += created
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  OK  schema={tenant.schema_name!r} "
                        f"created={created} (tenant: {tenant.name})"
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
