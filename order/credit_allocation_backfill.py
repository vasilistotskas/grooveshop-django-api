"""Allocate money that was booked without an allocation.

Every source is allocated when it is created (``order.credit_allocation``).
The exceptions are the sources a release that did not know about allocations
booked while it still served after the migration that created the table, and
anything a failure left behind. ``order.tasks.allocate_credit_sources`` runs
:func:`backfill_credit_allocations` periodically to allocate them:

* a refund booked without a claim (a provider's webhook) and not credited;
* a provider refund attempt not declined, not credited;
* a chargeback decision that the goods came back, not credited.

It runs on the live models and writes through the same functions as the
code that books money (``credit_allocation.allocate_source``), so there is
one definition of the headroom, of the duplicate-payment excess and of what
"allocated" means: pro rata over the headroom of the moment, capped to it,
oldest first, each order under its row lock. A source that owes a credit is
allocated exactly once and is then left alone (a source that owes nothing is
marked by a row of amount zero), so a run finds nothing once every release
in production allocates at claim time.

Money that moved before its order's receipt row existed is left alone: the
receipt was issued net of it and owes no credit. Stored-value attempts need
the gift-card and points ledgers and are allocated when claimed.

The duplicate-payment excess is read as it stands now, as if the refund were
the latest (the excess it was booked against is not recorded); that only
matters for a refund of an order paid twice that the old release booked.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from django.db import transaction
from django.db.models import Q

from order import credit_allocation
from order.enum.payment_transaction import PaymentTransactionKind
from order.models import (
    ChargebackDecision,
    Invoice,
    Order,
    PaymentTransaction,
)
from order.models.refund_attempt import (
    RefundAttempt,
    RefundAttemptKind,
    RefundAttemptStatus,
)
from order.payment_ledger import PaymentLedger

logger = logging.getLogger(__name__)

_ZERO = Decimal(0)


def _owing() -> dict[int, list]:
    """Every source that needs an allocation, by order:
    ``(moment, rank, source, amount, excess)`` where *excess* is ``None``
    (a duplicate payment cannot be part of it) or the keyword arguments for
    ``PaymentLedger.refundable_excess``."""
    invoices = {i.order_id: i for i in Invoice.objects.all()}
    by_order: dict[int, list] = {}

    def add(order_id, moment, rank, source, amount, excess):
        invoice = invoices.get(order_id)
        if (
            invoice is not None
            and moment is not None
            and moment < invoice.created_at
        ):
            return
        if amount > 0:
            by_order.setdefault(order_id, []).append(
                (moment, rank, source, amount, excess)
            )

    for refund in PaymentTransaction.objects.filter(
        kind=PaymentTransactionKind.REFUND,
        attempt__isnull=True,
        credit_document__isnull=True,
        allocations__isnull=True,
    ):
        add(
            refund.order_id,
            refund.created_at,
            "r",
            {"refund": refund},
            Decimal(refund.amount.amount)
            - credit_allocation.reversed_of(refund),
            {
                "add_back": Decimal(refund.amount.amount)
                - credit_allocation.reversed_of(refund)
            },
        )
    for attempt in (
        RefundAttempt.objects.filter(
            # No refund yet: only a claim still in flight holds room. A
            # SETTLED attempt without one answered a refund another attempt
            # owns and holds nothing.
            Q(refund__isnull=True, status=RefundAttemptStatus.PENDING)
            | Q(refund__isnull=False, refund__credit_document__isnull=True),
            kind=RefundAttemptKind.PROVIDER,
            credit_document__isnull=True,
            allocations__isnull=True,
        )
        .exclude(status=RefundAttemptStatus.DECLINED)
        .select_related("refund")
    ):
        refund = attempt.refund
        add(
            attempt.order_id,
            refund.created_at if refund else None,
            "a",
            {"refund_attempt": attempt},
            Decimal(refund.amount.amount)
            - credit_allocation.reversed_of(refund)
            if refund
            else Decimal(attempt.amount.amount),
            {
                "add_back": Decimal(refund.amount.amount)
                - credit_allocation.reversed_of(refund)
                if refund
                else _ZERO,
                "exclude": [attempt.pk],
            },
        )
    for decision in ChargebackDecision.objects.filter(
        goods_returned=True,
        chargeback__chargeback_credit__isnull=True,
        allocations__isnull=True,
    ).select_related("chargeback"):
        add(
            decision.chargeback.order_id,
            decision.decided_at,
            "d",
            {"decision": decision},
            Decimal(decision.chargeback.amount.amount),
            None,
        )
    return by_order


def backfill_credit_allocations() -> int:
    """Allocate every source that owes a credit and has no allocation;
    returns how many sources were given one."""
    done = 0
    for order_id, sources in _owing().items():
        with transaction.atomic():
            order = Order.objects.select_for_update().get(pk=order_id)
            for moment, _rank, source, amount, excess_of in sorted(
                sources, key=lambda s: (s[0] is None, s[0], s[1])
            ):
                if credit_allocation.is_allocated(**source):
                    continue
                excess = (
                    PaymentLedger.refundable_excess(order, **excess_of)
                    if excess_of is not None
                    else _ZERO
                )
                credit_allocation.allocate_source(
                    order, amount, moved_at=moment, excess=excess, **source
                )
                done += 1
    return done
