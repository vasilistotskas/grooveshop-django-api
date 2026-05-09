"""Management command: backfill_extra_settings_defaults

Iterates all active non-public tenants and calls
``Setting.set_defaults_from_settings()`` inside each tenant's schema
context.  This backfills any entries added to ``EXTRA_SETTINGS_DEFAULTS``
in settings.py that are not yet present in existing tenant databases.

Usage:
    uv run python manage.py backfill_extra_settings_defaults
    uv run python manage.py backfill_extra_settings_defaults --schema webside
    uv run python manage.py backfill_extra_settings_defaults --dry-run

Safe to re-run:``set_defaults_from_settings`` is idempotent — it only
creates missing rows and leaves existing values untouched.
"""

from __future__ import annotations

import logging

from django.core.management.base import BaseCommand
from django_tenants.utils import get_public_schema_name, schema_context

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Backfill EXTRA_SETTINGS_DEFAULTS entries across all active tenants.

    Useful after adding new rows to EXTRA_SETTINGS_DEFAULTS in settings.py:
    running this command propagates the defaults into every tenant that was
    created before the new entry was added, without touching existing values.
    """

    help = (
        "Backfill missing EXTRA_SETTINGS_DEFAULTS entries into all active "
        "tenant schemas (or a single --schema). Idempotent — skips rows "
        "that already exist."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--schema",
            default=None,
            help=(
                "Limit backfill to a single tenant schema name. "
                "Defaults to all active non-public tenants."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help=(
                "Print which tenants would be backfilled without "
                "making any changes."
            ),
        )

    def handle(self, *args, **options):
        from tenant.models import Tenant

        target_schema = options["schema"]
        dry_run = options["dry_run"]
        public_schema = get_public_schema_name()

        if target_schema:
            tenants = Tenant.objects.filter(
                schema_name=target_schema,
                is_active=True,
            ).exclude(schema_name=public_schema)
            if not tenants.exists():
                self.stdout.write(
                    self.style.WARNING(
                        f"No active non-public tenant with "
                        f"schema_name='{target_schema}' found."
                    )
                )
                return
        else:
            tenants = Tenant.objects.filter(is_active=True).exclude(
                schema_name=public_schema
            )

        total = tenants.count()
        if total == 0:
            self.stdout.write(
                self.style.WARNING("No active tenants to backfill.")
            )
            return

        self.stdout.write(
            f"{'[DRY RUN] ' if dry_run else ''}Backfilling {total} tenant(s)…"
        )

        ok = 0
        failed = 0

        for tenant in tenants:
            if dry_run:
                self.stdout.write(
                    f"  WOULD backfill schema='{tenant.schema_name}' "
                    f"(tenant: {tenant.name})"
                )
                ok += 1
                continue

            try:
                with schema_context(tenant.schema_name):
                    from extra_settings.models import Setting

                    Setting.set_defaults_from_settings()

                self.stdout.write(
                    self.style.SUCCESS(
                        f"  OK  schema='{tenant.schema_name}' "
                        f"(tenant: {tenant.name})"
                    )
                )
                ok += 1
            except Exception as exc:
                self.stderr.write(
                    self.style.ERROR(
                        f"  FAIL schema='{tenant.schema_name}' "
                        f"(tenant: {tenant.name}): {exc}"
                    )
                )
                logger.exception(
                    "backfill_extra_settings_defaults failed for schema=%s",
                    tenant.schema_name,
                )
                failed += 1

        status_style = self.style.SUCCESS if failed == 0 else self.style.WARNING
        self.stdout.write(
            status_style(
                f"\n{'[DRY RUN] ' if dry_run else ''}"
                f"Done: {ok} succeeded, {failed} failed."
            )
        )
