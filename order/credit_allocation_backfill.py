"""Allocate the money that was booked before allocations existed.

A credit document is read off its own VAT rows from the moment it exists
(``order.credit_allocation.headroom``), so what needs an allocation is every
source that still OWES a credit, or will be netted off a receipt that does
not exist yet:

* a refund booked without a claim (a provider's webhook) and not credited;
* a provider refund attempt not declined, not credited;
* a chargeback decision that the goods came back, not credited.

Each is given what ``order.credit_allocation`` would have given it when it
was booked: pro rata over the headroom of that moment, oldest first, capped
to it. Allocating them here, once, is what lets every read path (the credit
reconciliation, the receipt render) stay free of writes.

Money that moved before its order's receipt row existed is left alone: the
receipt was issued net of it and owes no credit. Stored-value attempts need
the gift-card and points ledgers; none exist on any store that issues
documents (production and staging held no refund attempt at all when this
was written, 2026-11-08).

Written against the app registry it is given, so the migration runs it on
the historical models and a test runs it on the real ones (the lines' VAT
come from the one live function, ``order.taxation.order_vat_lines``, as in
``order.vat_snapshot``). Idempotent, and safe while the previous release
still writes: a source it books afterwards has no allocation and is
reported by ``create_pending_credits`` instead of being credited wrongly.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from django.db.models import Q

from order.discounts import allocate_capped
from order.taxation import breakdown_by_rate, order_vat_lines

logger = logging.getLogger(__name__)

_ZERO = Decimal(0)
_CANCELED = "CANCELED"
_REFUND = "REFUND"
_REFUND_REVERSAL = "REFUND_REVERSAL"


def _reversed(models, refund) -> Decimal:
    """What the provider reported failed of *refund*, in all."""
    return sum(
        (
            Decimal(row.amount.amount)
            for row in models["transaction"].objects.filter(
                provider=refund.provider,
                provider_reference=refund.provider_reference,
                kind=_REFUND_REVERSAL,
            )
        ),
        _ZERO,
    )


def _headroom(models, order, invoice) -> dict[Decimal, Decimal]:
    """The headroom of ``order.credit_allocation`` on the registry's models."""
    rows = (
        invoice.vat_breakdown
        if invoice is not None
        else breakdown_by_rate(order_vat_lines(order))
    )
    left = {Decimal(r["rate"]): Decimal(r["gross"]) for r in rows}
    held = (
        models["allocation"]
        .objects.filter(order_id=order.pk)
        .exclude(refund_attempt__status="DECLINED")
        .exclude(decision__goods_returned=False)
    )
    if invoice is not None:
        held = held.filter(
            Q(moved_at__isnull=True) | Q(moved_at__gte=invoice.created_at)
        )
    for row in held:
        left[row.rate] = left.get(row.rate, _ZERO) - Decimal(row.amount.amount)
    if invoice is not None:
        # The documents themselves count, whatever their source holds.
        for credit in (
            models["credit"]
            .objects.filter(invoice_id=invoice.pk)
            .exclude(mydata_status=_CANCELED)
        ):
            for row in credit.vat_breakdown:
                rate = Decimal(row["rate"])
                left[rate] = left.get(rate, _ZERO) - Decimal(row["gross"])
    return left


def _owing(models, invoices) -> dict[int, list]:
    """Every source that needs an allocation, by order:
    ``(moment, tiebreak, source, amount)``."""
    by_order: dict[int, list] = {}

    def add(order_id, moment, rank, source, amount):
        invoice = invoices.get(order_id)
        if (
            invoice is not None
            and moment is not None
            and moment < invoice.created_at
        ):
            return
        if amount > 0:
            by_order.setdefault(order_id, []).append(
                (moment, rank, source, amount)
            )

    for refund in models["transaction"].objects.filter(
        kind=_REFUND,
        attempt__isnull=True,
        credit_document__isnull=True,
        allocations__isnull=True,
    ):
        add(
            refund.order_id,
            refund.created_at,
            "r",
            {"refund_id": refund.pk},
            Decimal(refund.amount.amount) - _reversed(models, refund),
        )
    for attempt in (
        models["attempt"]
        .objects.filter(
            Q(refund__isnull=True) | Q(refund__credit_document__isnull=True),
            kind="PROVIDER",
            credit_document__isnull=True,
            allocations__isnull=True,
        )
        .exclude(status="DECLINED")
        .select_related("refund")
    ):
        refund = attempt.refund
        add(
            attempt.order_id,
            refund.created_at if refund else None,
            "a",
            {"refund_attempt_id": attempt.pk},
            Decimal(refund.amount.amount) - _reversed(models, refund)
            if refund
            else Decimal(attempt.amount.amount),
        )
    for decision in (
        models["decision"]
        .objects.filter(
            goods_returned=True,
            chargeback__chargeback_credit__isnull=True,
            allocations__isnull=True,
        )
        .select_related("chargeback")
    ):
        add(
            decision.chargeback.order_id,
            decision.decided_at,
            "d",
            {"decision_id": decision.pk},
            Decimal(decision.chargeback.amount.amount),
        )
    return by_order


def backfill_credit_allocations(apps) -> int:
    """Allocate every source that owes a credit and has no allocation;
    returns how many sources were given one."""
    models = {
        "order": apps.get_model("order", "Order"),
        "invoice": apps.get_model("order", "Invoice"),
        "credit": apps.get_model("order", "RetailCredit"),
        "allocation": apps.get_model("order", "CreditAllocation"),
        "attempt": apps.get_model("order", "RefundAttempt"),
        "decision": apps.get_model("order", "ChargebackDecision"),
        "transaction": apps.get_model("order", "PaymentTransaction"),
    }
    invoices = {
        invoice.order_id: invoice for invoice in models["invoice"].objects.all()
    }
    done = 0
    for order_id, sources in _owing(models, invoices).items():
        order = (
            models["order"].objects.prefetch_related("items").get(pk=order_id)
        )
        try:
            left = _headroom(models, order, invoices.get(order_id))
        except ValueError:
            logger.warning("Order %s: no VAT lines derivable", order_id)
            continue
        for moment, _rank, source, amount in sorted(
            sources, key=lambda s: (s[0] is None, s[0], s[1])
        ):
            room = {rate: gross for rate, gross in left.items() if gross > 0}
            shares = allocate_capped(amount, room, room) if room else {}
            rows = [
                models["allocation"](
                    order_id=order_id,
                    rate=rate,
                    amount=share,
                    amount_currency=order.amount_due_currency,
                    moved_at=moment,
                    **source,
                )
                for rate, share in shares.items()
                if share > 0
            ]
            models["allocation"].objects.bulk_create(rows)
            for rate, share in shares.items():
                left[rate] = left.get(rate, _ZERO) - share
            done += 1
    return done
