"""Snapshot the VAT lines and the ship moment of orders that have none.

Run it once per rollout of ``OrderVatLine`` / ``Order.shipped_at``, after
the last pod of the previous release has exited: that release keeps
creating and shipping orders on the migrated schema without writing
either (``order.vat_snapshot`` says what is written and why). Idempotent.
"""

from __future__ import annotations

from django.apps import apps
from django.core.management.base import BaseCommand

from core.management.tenant_mixin import TenantCommandMixin
from order.vat_snapshot import backfill_shipped_at, backfill_vat_lines


class Command(TenantCommandMixin, BaseCommand):
    help = (
        "Write the VAT lines and the shipped-at moment of orders created "
        "or shipped before the turnover snapshots existed."
    )

    require_tenant_scope = True

    def add_arguments(self, parser):
        self.add_tenant_arguments(parser)

    def handle(self, *args, **options):
        for ctx in self.iter_tenant_contexts(options):
            with ctx:
                written, skipped = backfill_vat_lines(apps)
                stamped = backfill_shipped_at(apps)
                self.stdout.write(
                    f"VAT lines written for {written} orders, "
                    f"{len(skipped)} skipped {skipped}; "
                    f"shipped_at stamped on {stamped} orders"
                )
