"""Snapshot the discount allocation of orders that have none.

Run it once per rollout of ``OrderItem.discount_allocation``, after the
last pod of the previous release has exited: that release keeps creating
orders on the migrated schema without writing the column, and the tax
documents refuse such an order until its lines carry the allocation
(``order.discount_snapshot`` says what is written and why). Idempotent, so
running it again, or with nothing to do, changes nothing.
"""

from __future__ import annotations

from django.apps import apps
from django.core.management.base import BaseCommand

from core.management.tenant_mixin import TenantCommandMixin
from order.discount_snapshot import heal_discount_allocation


class Command(TenantCommandMixin, BaseCommand):
    help = (
        "Give orders whose lines carry no discount allocation (created "
        "before the column, or by the previous release during a rollout) "
        "the proportional allocation their documents were issued under."
    )

    require_tenant_scope = True

    def add_arguments(self, parser):
        self.add_tenant_arguments(parser)

    def handle(self, *args, **options):
        for ctx in self.iter_tenant_contexts(options):
            with ctx:
                healed = heal_discount_allocation(apps)
                self.stdout.write(f"Orders healed: {healed}")
