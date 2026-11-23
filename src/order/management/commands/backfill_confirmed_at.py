"""Stamp ``Order.confirmed_at`` on orders confirmed without it.

Run once per store after the release that adds the column has fully
rolled out: the previous release keeps creating and settling orders on the
migrated schema without writing it (``order.lifecycle.confirmation``).
Idempotent, and it tells no one: those orders' emails went out already.

The new release never stores an order paid without confirming it in the
same transaction (``PayWayService.process_payment``'s ``on_paid``, the
settle paths), so a run cannot stamp an order whose confirmation is still
to come.
"""

from __future__ import annotations

from django.apps import apps
from django.core.management.base import BaseCommand

from core.management.tenant_mixin import TenantCommandMixin
from order.lifecycle.confirmation import backfill_confirmed_at


class Command(TenantCommandMixin, BaseCommand):
    help = (
        "Stamp the confirmed-at moment of orders the previous release "
        "confirmed without it."
    )

    require_tenant_scope = True
    include_suspended_stores = True

    def add_arguments(self, parser):
        self.add_tenant_arguments(parser)

    def handle(self, *args, **options):
        for ctx in self.iter_tenant_contexts(options):
            with ctx:
                stamped = backfill_confirmed_at(apps)
                self.stdout.write(f"confirmed_at stamped on {stamped} orders")
