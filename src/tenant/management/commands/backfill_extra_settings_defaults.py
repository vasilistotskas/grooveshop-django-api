"""Management command: backfill_extra_settings_defaults

Iterates every store, suspended ones included, and calls
``Setting.set_defaults_from_settings()`` inside each tenant's schema
context.  This backfills any entries added to ``EXTRA_SETTINGS_DEFAULTS``
in src/settings/store_settings.py that are not yet present in existing tenant
databases. A suspended store is backfilled too: this brings its data up
to date with the code and starts no work, so a store suspended through a
few deploys has every setting when it is reactivated.

Usage:
    uv run python manage.py backfill_extra_settings_defaults
    uv run python manage.py backfill_extra_settings_defaults --schema webside
    uv run python manage.py backfill_extra_settings_defaults --dry-run

Safe to re-run:``set_defaults_from_settings`` is idempotent — it only
creates missing rows and leaves existing values untouched.

It also brings every existing row's description and validator in line
with src/settings/store_settings.py
(``tenant.settings_metadata.sync_setting_metadata``, which ``migrate``
runs too), never its value.
"""

from __future__ import annotations

import logging

from django.core.management.base import BaseCommand
from django_tenants.utils import schema_context

from core.management.tenant_mixin import TenantCommandMixin

logger = logging.getLogger(__name__)


class Command(TenantCommandMixin, BaseCommand):
    """Backfill EXTRA_SETTINGS_DEFAULTS entries across every store.

    Useful after adding new rows to EXTRA_SETTINGS_DEFAULTS in
    src/settings/store_settings.py: running this command propagates the
    defaults into every tenant that was created before the new entry was
    added, without touching existing values.
    """

    help = (
        "Backfill missing EXTRA_SETTINGS_DEFAULTS entries into every "
        "store's schema, suspended ones included (or a single --schema). "
        "Idempotent — skips rows "
        "that already exist."
    )

    include_suspended_stores = True

    def add_arguments(self, parser):
        parser.add_argument(
            "--schema",
            default=None,
            help=(
                "Limit backfill to a single store's schema name. "
                "Defaults to every store."
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
        target_schema = options["schema"]
        dry_run = options["dry_run"]
        if target_schema:
            tenants = self.target_stores().filter(schema_name=target_schema)
            if not tenants.exists():
                self.stdout.write(
                    self.style.WARNING(
                        f"No store with schema_name='{target_schema}' found."
                    )
                )
                return
        else:
            tenants = self.target_stores()

        total = tenants.count()
        if total == 0:
            self.stdout.write(self.style.WARNING("No stores to backfill."))
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

                    from tenant.settings_metadata import sync_setting_metadata

                    Setting.set_defaults_from_settings()
                    sync_setting_metadata()

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
