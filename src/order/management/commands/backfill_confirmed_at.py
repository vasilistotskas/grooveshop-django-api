"""Stamp ``Order.confirmed_at`` on orders confirmed without it.

Run it once per store after the release that adds the column has fully
rolled out: the previous release kept creating and settling orders on the
migrated schema without writing it. It stamps the unconfirmed orders whose
confirmation email went out (``order.lifecycle.confirmation.
backfill_confirmed_at``), which only the previous release leaves; every
other order is the new release's to confirm. Idempotent, and it tells no
one: those orders' emails went out already.
"""

from __future__ import annotations

from django.apps import apps
from django.core.management.base import BaseCommand

from core.management.tenant_mixin import TenantCommandMixin
from order.lifecycle.confirmation import backfill_confirmed_at


class Command(TenantCommandMixin, BaseCommand):
    help = (
        "Stamp the confirmed-at moment of orders the previous release "
        "announced without it."
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
