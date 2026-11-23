"""Stamp ``Order.confirmed_at`` on orders confirmed without it.

Run it once per store after the release that adds the column has fully
rolled out, with ``--rollout-finished-at`` set to the moment the rollout
finished: the previous release kept creating and settling orders on the
migrated schema without writing it. It stamps the offline orders placed
before then and the online orders whose payment was booked before then
(``order.lifecycle.confirmation.backfill_confirmed_at``); an online order
paid since is the new release's to confirm. Idempotent, and it tells no
one: those orders' emails went out already.
"""

from __future__ import annotations

from datetime import datetime

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError

from core.management.tenant_mixin import TenantCommandMixin
from order.lifecycle.confirmation import backfill_confirmed_at


class Command(TenantCommandMixin, BaseCommand):
    help = (
        "Stamp the confirmed-at moment of orders the previous release "
        "announced without it before the rollout finished."
    )

    require_tenant_scope = True
    include_suspended_stores = True

    def add_arguments(self, parser):
        self.add_tenant_arguments(parser)
        parser.add_argument(
            "--rollout-finished-at",
            required=True,
            help=(
                "ISO 8601 time with its offset: when the release that "
                "confirms orders finished rolling out. Offline orders "
                "placed and online payments booked before it are stamped."
            ),
        )

    def handle(self, *args, **options):
        try:
            rollout_finished_at = datetime.fromisoformat(
                options["rollout_finished_at"]
            )
        except ValueError as e:
            raise CommandError(f"--rollout-finished-at: {e}") from e
        if rollout_finished_at.tzinfo is None:
            raise CommandError(
                "--rollout-finished-at needs an offset, "
                "e.g. 2026-11-23T05:00:00Z"
            )
        for ctx in self.iter_tenant_contexts(options):
            with ctx:
                stamped = backfill_confirmed_at(
                    apps, rollout_finished_at=rollout_finished_at
                )
                self.stdout.write(f"confirmed_at stamped on {stamped} orders")
