"""Snapshot the product name and SKU of order lines that have none.

Run it once per rollout of ``OrderItem.product_name`` / ``OrderItem.sku``,
after the last pod of the previous release has exited: that release keeps
creating lines on the migrated schema without writing the columns, and an
invoice prints a blank name for such a line until it is filled in
(``order.line_snapshot`` says what is written and why). Idempotent, so
running it again, or with nothing to do, changes nothing.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import connection

from core.management.tenant_mixin import TenantCommandMixin
from order.line_snapshot import backfill_line_snapshots


class Command(TenantCommandMixin, BaseCommand):
    help = (
        "Give order lines with no product name and SKU snapshot (created "
        "before the columns, or by the previous release during a rollout) "
        "the name and SKU their product has now."
    )

    require_tenant_scope = True

    def add_arguments(self, parser):
        self.add_tenant_arguments(parser)

    def handle(self, *args, **options):
        for ctx in self.iter_tenant_contexts(options):
            with ctx:
                written = backfill_line_snapshots(connection)
                self.stdout.write(f"Lines written: {written}")
