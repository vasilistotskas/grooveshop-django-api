"""Stamp ``Order.confirmed_at`` on orders confirmed without it.

Run it once per store after the release that adds the column has fully
rolled out, with ``--created-before`` set to the moment the rollout
finished: the previous release kept creating and settling orders on the
migrated schema without writing it (``order.lifecycle.confirmation``),
while every order created since is confirmed by the new release.
Idempotent, and it tells no one: those orders' emails went out already.
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
        "confirmed without it, created before the rollout finished."
    )

    require_tenant_scope = True
    include_suspended_stores = True

    def add_arguments(self, parser):
        self.add_tenant_arguments(parser)
        parser.add_argument(
            "--created-before",
            required=True,
            help=(
                "ISO 8601 time with its offset: when the release that "
                "confirms orders finished rolling out. Only orders created "
                "before it are stamped."
            ),
        )

    def handle(self, *args, **options):
        try:
            created_before = datetime.fromisoformat(options["created_before"])
        except ValueError as e:
            raise CommandError(f"--created-before: {e}") from e
        if created_before.tzinfo is None:
            raise CommandError(
                "--created-before needs an offset, e.g. 2026-11-23T05:00:00Z"
            )
        for ctx in self.iter_tenant_contexts(options):
            with ctx:
                stamped = backfill_confirmed_at(
                    apps, created_before=created_before
                )
                self.stdout.write(f"confirmed_at stamped on {stamped} orders")
