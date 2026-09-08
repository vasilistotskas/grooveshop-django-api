"""Reconcile ACS COD payouts for a date range.

The nightly beat task (``reconcile_acs_cod_payouts``) only queries
yesterday's payout date. When reconciliation was broken or paused for a
period, the missed dates never get re-queried — this command replays
them. Primary use: the 2026-07-11 backfill after the ``POD`` column
mapping fix, where 10 weeks of payouts were fetched nightly but never
persisted.

Examples::

    python manage.py reconcile_acs_cod                  # yesterday only
    python manage.py reconcile_acs_cod --days 90        # last 90 days
    python manage.py reconcile_acs_cod --days 90 --silent

``--silent`` suppresses the customer-facing COMPLETED email/toast for
orders whose payment flips during the run — mandatory etiquette for
backfills, where the delivery happened weeks ago and a sudden
"order completed" notification would only confuse customers. Internal
state (order_paid signal, status transition, history) still flows.
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import connection
from django.utils import timezone

from core.management.tenant_mixin import TenantCommandMixin
from shipping_acs.config import is_configured
from shipping_acs.services import AcsService


class Command(TenantCommandMixin, BaseCommand):
    help = (
        "Reconcile ACS COD payouts for the last N days "
        "(ACS_COD_Beneficiary_Info is queried once per date)."
    )

    # Orders/shipments AND the ACS credentials are per-tenant — running
    # from the public context would read clone-legacy rows with NO
    # credentials. The scheduled path
    # (fanout_reconcile_acs_cod_payouts -> reconcile_acs_cod_payouts
    # under TenantTask) is already tenant-scoped.
    require_tenant_scope = True

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=1,
            help="How many days back to reconcile, ending yesterday.",
        )
        parser.add_argument(
            "--silent",
            action="store_true",
            help=(
                "Suppress customer-facing COMPLETED notifications for "
                "orders flipped by this run (use for backfills)."
            ),
        )

        self.add_tenant_arguments(parser)

    def handle(self, *args, **options):
        for ctx in self.iter_tenant_contexts(options):
            with ctx:
                self._handle_for_schema(*args, **options)

    def _handle_for_schema(self, *args, **options):
        days: int = options["days"]
        silent: bool = options["silent"]
        if days < 1:
            self.stderr.write(self.style.ERROR("--days must be >= 1"))
            return

        # ``--all-tenants`` targets every ACTIVE tenant, not every tenant
        # that sells through ACS — 3 of the 4 in production have no ACS
        # credentials at all. Without this guard the first of them raises
        # ``AcsConfigError`` out of ``AcsClient.__init__`` and aborts the
        # whole run before reaching the one tenant that needed
        # reconciling. Skip cleanly and say so, exactly as the beat
        # task's ``_skip_if_acs_unconfigured`` does.
        if not is_configured():
            self.stdout.write(
                self.style.WARNING(
                    f"ACS is not configured for schema "
                    f"'{connection.schema_name}' — skipping."
                )
            )
            return

        yesterday = (timezone.localtime() - timedelta(days=1)).date()
        # Keys drive the accumulation below, so a counter missing here is
        # silently dropped from the summary.
        totals = {
            "upserted": 0,
            "linked": 0,
            "unmatched": 0,
            "skipped": 0,
            "rows": 0,
        }

        # Oldest first so payment flips replay in chronological order.
        for offset in range(days - 1, -1, -1):
            payment_date = yesterday - timedelta(days=offset)
            result = AcsService.reconcile_cod_payouts(
                cod_payment_date=payment_date,
                silent_for_customer=silent,
            )
            for key in totals:
                totals[key] += result.get(key, 0)
            # Either kind of unattributable row is money we received and
            # cannot tie to an order — the operator needs to SEE that in
            # the run output, not just in the alert email.
            style = (
                self.style.WARNING
                if result.get("skipped") or result.get("unmatched")
                else self.style.SUCCESS
            )
            self.stdout.write(style(f"{payment_date}: {result}"))

        style = (
            self.style.WARNING
            if totals["skipped"] or totals["unmatched"]
            else self.style.SUCCESS
        )
        self.stdout.write(style(f"Totals: {totals}"))
        if totals["unmatched"] or totals["skipped"]:
            self.stdout.write(
                self.style.WARNING(
                    "Unattributable payout rows were persisted but not tied "
                    "to an order. Grep the worker log for "
                    "'payout matches no shipment' for each row's voucher, "
                    "amount and receiver."
                )
            )
