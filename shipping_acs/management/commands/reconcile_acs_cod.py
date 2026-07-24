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
from django.utils import timezone

from shipping_acs.services import AcsService


class Command(BaseCommand):
    help = (
        "Reconcile ACS COD payouts for the last N days "
        "(ACS_COD_Beneficiary_Info is queried once per date)."
    )

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

    def handle(self, *args, **options):
        days: int = options["days"]
        silent: bool = options["silent"]
        if days < 1:
            self.stderr.write(self.style.ERROR("--days must be >= 1"))
            return

        yesterday = (timezone.localtime() - timedelta(days=1)).date()
        totals = {"upserted": 0, "linked": 0, "skipped": 0, "rows": 0}

        # Oldest first so payment flips replay in chronological order.
        for offset in range(days - 1, -1, -1):
            payment_date = yesterday - timedelta(days=offset)
            result = AcsService.reconcile_cod_payouts(
                cod_payment_date=payment_date,
                silent_for_customer=silent,
            )
            for key in totals:
                totals[key] += result.get(key, 0)
            style = (
                self.style.WARNING
                if result.get("skipped")
                else self.style.SUCCESS
            )
            self.stdout.write(style(f"{payment_date}: {result}"))

        self.stdout.write(self.style.SUCCESS(f"Totals: {totals}"))
