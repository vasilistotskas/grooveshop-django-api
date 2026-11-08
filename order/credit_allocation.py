"""The VAT split of every credit, decided when the money is claimed.

See :mod:`order.models.credit_allocation` for the rule. This module is the
one writer of ``CreditAllocation`` rows and the one reader of the room that
is left on the receipt:

* :func:`headroom` is a plain sum: the receipt's gross per rate (the order's
  VAT lines while there is no receipt), less the allocations of every source
  that was not declined and whose money moves at or after the receipt (an
  in-flight claim included), less the credits of anything that has no
  allocation.
* :func:`allocate_pro_rata` spreads an amount over that headroom, to the
  cent, capped to it; a claim that does not fit is refused by its caller
  before any money moves.
* :func:`ensure_allocations` gives an allocation to a source created before
  allocations existed, or booked by a path that never claimed (a provider's
  webhook): pro rata over the headroom at that moment, under the order lock,
  oldest first. This is the one documented rule for money nobody claimed.

Everything here runs under the order's row lock (the callers hold it).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from django.db.models import Q
from djmoney.money import Money

from order.discounts import allocate_capped, proportional_shares
from order.enum.payment_transaction import PaymentTransactionKind
from order.models import (
    ChargebackDecision,
    CreditAllocation,
    Invoice,
    Order,
    PaymentTransaction,
    RetailCredit,
)
from order.models.invoice import MyDataStatus
from order.models.refund_attempt import (
    RefundAttempt,
    RefundAttemptKind,
    RefundAttemptStatus,
)
from order.taxation import ChargeKind, breakdown_by_rate, order_vat_lines

_ZERO = Decimal(0)


@dataclass(frozen=True)
class Piece:
    """A part of a refund that belongs to one VAT rate (gross)."""

    kind: str
    rate: Decimal
    amount: Decimal
    item_pk: int | None = None
    quantity: int = 0


def _base(order: Order) -> tuple[dict[Decimal, Decimal], Invoice | None]:
    """The gross per rate there is to credit, and the receipt row if any."""
    invoice = Invoice.objects.filter(order=order).first()
    if invoice is not None:
        rows = invoice.vat_breakdown
    else:
        try:
            rows = breakdown_by_rate(order_vat_lines(order))
        except ValueError:
            rows = []
    return {Decimal(r["rate"]): Decimal(r["gross"]) for r in rows}, invoice


def _has_allocation(**source) -> bool:
    return CreditAllocation.objects.filter(**source).exists()


def headroom(order: Order) -> dict[Decimal, Decimal]:
    """The gross each VAT rate can still be credited. See the module doc."""
    left, invoice = _base(order)
    counting = CreditAllocation.objects.counting().filter(order=order)
    if invoice is not None:
        counting = counting.filter(
            Q(moved_at__isnull=True) | Q(moved_at__gte=invoice.created_at)
        )
    for row in counting:
        left[row.rate] = left.get(row.rate, _ZERO) - Decimal(row.amount.amount)
    if invoice is not None:
        for credit in RetailCredit.objects.filter(invoice=invoice).exclude(
            mydata_status=MyDataStatus.CANCELED
        ):
            if _credit_has_allocation(credit):
                continue
            for row in credit.vat_breakdown:
                rate = Decimal(row["rate"])
                left[rate] = left.get(rate, _ZERO) - Decimal(row["gross"])
    return left


def _credit_has_allocation(credit: RetailCredit) -> bool:
    if credit.refund_id is not None:
        attempt = RefundAttempt.objects.filter(refund_id=credit.refund_id)
        return _has_allocation(refund_id=credit.refund_id) or (
            CreditAllocation.objects.filter(refund_attempt__in=attempt).exists()
        )
    if credit.refund_attempt_id is not None:
        return _has_allocation(refund_attempt_id=credit.refund_attempt_id)
    if credit.chargeback_id is not None:
        return CreditAllocation.objects.filter(
            decision__chargeback_id=credit.chargeback_id
        ).exists()
    return False


def allocate_pro_rata(order: Order, amount: Decimal) -> dict[Decimal, Decimal]:
    """*amount* spread over the headroom, capped to it; the result sums to
    less than *amount* when the headroom cannot hold it all."""
    left = {rate: gross for rate, gross in headroom(order).items() if gross > 0}
    if not left:
        return {}
    shares = allocate_capped(amount, left, left)
    return {rate: share for rate, share in shares.items() if share > 0}


def headroom_total(order: Order) -> Decimal:
    return sum((max(v, _ZERO) for v in headroom(order).values()), _ZERO)


def has_rates(order: Order) -> bool:
    """Whether there is anything to allocate against: an order without
    taxable lines (and no receipt) has no rates, and owes no credit."""
    return bool(_base(order)[0])


def pieces_of(allocation: Mapping[Decimal, Decimal]) -> list[Piece]:
    return [
        Piece(kind="", rate=rate, amount=amount)
        for rate, amount in sorted(allocation.items(), reverse=True)
    ]


def split_over_shares(
    pieces: Sequence[Piece], shares: Sequence[Decimal]
) -> list[list[Piece]]:
    """Lay *pieces* over *shares* in order (they total the same); a piece
    cut at the end of a share becomes two parts of the same rate and line,
    each carrying the piece's ``quantity`` and its own value."""
    remaining = list(pieces)
    out: list[list[Piece]] = []
    for share in shares:
        take: list[Piece] = []
        left = share
        while left > 0 and remaining:
            piece = remaining[0]
            if piece.amount <= left:
                take.append(piece)
                left -= piece.amount
                remaining.pop(0)
                continue
            take.append(
                Piece(
                    kind=piece.kind,
                    rate=piece.rate,
                    amount=left,
                    item_pk=piece.item_pk,
                    quantity=piece.quantity,
                )
            )
            remaining[0] = Piece(
                kind=piece.kind,
                rate=piece.rate,
                amount=piece.amount - left,
                item_pk=piece.item_pk,
                quantity=piece.quantity,
            )
            left = _ZERO
        out.append(take)
    return out


def store(
    order: Order,
    pieces: Sequence[Piece],
    *,
    moved_at: datetime | None = None,
    **source,
) -> None:
    """Write *pieces* as the allocation of one source (``refund_attempt=``,
    ``refund=`` or ``decision=``)."""
    currency = order.amount_due.currency
    CreditAllocation.objects.bulk_create(
        CreditAllocation(
            order=order,
            kind=piece.kind,
            item_id=piece.item_pk,
            rate=piece.rate,
            quantity=piece.quantity,
            amount=Money(piece.amount, currency),
            moved_at=moved_at,
            **source,
        )
        for piece in pieces
        if piece.amount > 0
    )


def allocation_of(**source) -> dict[Decimal, Decimal]:
    """The gross per rate stored for one source."""
    out: dict[Decimal, Decimal] = {}
    for row in CreditAllocation.objects.filter(**source):
        out[row.rate] = out.get(row.rate, _ZERO) + Decimal(row.amount.amount)
    return out


def allocation_of_refund(refund: PaymentTransaction) -> dict[Decimal, Decimal]:
    """The allocation a booked refund's credit is issued from: its
    attempt's when an attempt claimed it, else its own."""
    attempt = RefundAttempt.objects.filter(refund=refund).first()
    if attempt is not None:
        return allocation_of(refund_attempt=attempt)
    return allocation_of(refund=refund)


def mark_moved(when: datetime, **source) -> None:
    CreditAllocation.objects.filter(**source).update(moved_at=when)


def scale(returned: Decimal, **source) -> None:
    """Make a source's rows total *returned* (less than they do): in
    proportion, rows left with nothing deleted. What was claimed and not
    returned is not held back any more."""
    rows = list(CreditAllocation.objects.filter(**source).order_by("pk"))
    claimed = sum((Decimal(r.amount.amount) for r in rows), _ZERO)
    if not rows or returned >= claimed:
        return
    shares = (
        proportional_shares(
            returned, {r.pk: Decimal(r.amount.amount) for r in rows}
        )
        if returned > 0
        else {}
    )
    for row in rows:
        share = shares.get(row.pk, _ZERO)
        if share > 0:
            row.amount = Money(share, row.amount.currency)
            row.save(update_fields=["amount", "amount_currency"])
        else:
            row.delete()


def _allocate_unclaimed(
    order: Order,
    amount: Decimal,
    when: datetime | None,
    **source,
) -> None:
    pieces = pieces_of(allocate_pro_rata(order, amount))
    store(order, pieces, moved_at=when, **source)


def book_refund(order: Order, refund: PaymentTransaction) -> None:
    """Allocate a refund that was booked without a claim (a provider's
    webhook): pro rata over the headroom now, capped to it."""
    if allocation_of(refund=refund) or not has_rates(order):
        return
    _allocate_unclaimed(
        order, Decimal(refund.amount.amount), refund.created_at, refund=refund
    )


def allocate_decision(order: Order, decision: ChargebackDecision) -> None:
    """The room a lost dispute's returned goods take on the receipt,
    decided now: the chargeback spread over the headroom, capped to it."""
    CreditAllocation.objects.filter(decision=decision).delete()
    if not decision.goods_returned or not has_rates(order):
        return
    _allocate_unclaimed(
        order,
        Decimal(decision.chargeback.amount.amount),
        decision.decided_at,
        decision=decision,
    )


def ensure_allocations(
    order: Order, *, claiming: Sequence[RefundAttempt] = ()
) -> None:
    """Allocate every source that owes a credit and has no allocation: a
    refund claimed or booked before allocations existed, or booked by a
    path that never claimed, oldest first, each pro rata over the headroom
    at that moment. A source whose credit was issued already keeps what it
    was credited (:func:`headroom` counts that credit). *claiming* are the
    attempts a claim is allocating right now: they are not unclaimed money."""
    if not has_rates(order):
        return
    pending: list[tuple[datetime, str, object, Decimal, datetime | None]] = []
    for attempt in (
        RefundAttempt.objects.filter(order=order, credit_document__isnull=True)
        .exclude(status=RefundAttemptStatus.DECLINED)
        .exclude(pk__in=[a.pk for a in claiming])
        .exclude(refund__credit_document__isnull=False)
    ):
        if _has_allocation(refund_attempt=attempt) or (
            attempt.refund is not None and _fully_reversed(attempt.refund)
        ):
            continue
        when, amount = _attempt_moment(attempt)
        if amount > 0:
            pending.append(
                (when or attempt.created_at, "a", attempt, amount, when)
            )
    for refund in PaymentTransaction.objects.filter(
        order=order,
        kind=PaymentTransactionKind.REFUND,
        credit_document__isnull=True,
        attempt__isnull=True,
    ):
        if not _has_allocation(refund=refund) and not _fully_reversed(refund):
            pending.append(
                (
                    refund.created_at,
                    "r",
                    refund,
                    Decimal(refund.amount.amount),
                    refund.created_at,
                )
            )
    for decision in ChargebackDecision.objects.filter(
        chargeback__order=order,
        goods_returned=True,
        chargeback__chargeback_credit__isnull=True,
    ):
        if not _has_allocation(decision=decision):
            pending.append(
                (
                    decision.decided_at,
                    "d",
                    decision,
                    Decimal(decision.chargeback.amount.amount),
                    decision.decided_at,
                )
            )
    for _key, kind, source, amount, when in sorted(
        pending, key=lambda p: (p[0], p[1])
    ):
        name = {"a": "refund_attempt", "r": "refund", "d": "decision"}[kind]
        _allocate_unclaimed(order, amount, when, **{name: source})


def _fully_reversed(refund: PaymentTransaction) -> bool:
    """Whether the provider reported this refund failed for all of it."""
    reversed_amount = sum(
        (
            Decimal(row.amount.amount)
            for row in PaymentTransaction.objects.filter(
                provider=refund.provider,
                provider_reference=refund.provider_reference,
                kind=PaymentTransactionKind.REFUND_REVERSAL,
            )
        ),
        _ZERO,
    )
    return reversed_amount >= Decimal(refund.amount.amount)


def _attempt_moment(
    attempt: RefundAttempt,
) -> tuple[datetime | None, Decimal]:
    """When an attempt's money moved (``None`` while in flight) and how
    much of it: a stored-value share counts what it really returned."""
    from order.payment_ledger import PaymentLedger

    if attempt.kind == RefundAttemptKind.STORED_VALUE:
        if attempt.status != RefundAttemptStatus.SETTLED:
            return None, Decimal(attempt.amount.amount)
        order = attempt.order
        return (
            PaymentLedger.stored_returned_at(attempt),
            Decimal(PaymentLedger.stored_returned_by(order, attempt).amount),
        )
    refund = attempt.refund
    return (
        refund.created_at if refund is not None else None,
        Decimal(attempt.amount.amount),
    )


def free_reversed(refund: PaymentTransaction, reversed_amount: Decimal) -> None:
    """A provider reported a booked refund failed: the money was never paid
    back, so its room on the receipt is free again, unless a credit
    document was issued for it (the document stays: it records what the
    merchant owes). Scales the allocation down by what was reversed."""
    attempt = RefundAttempt.objects.filter(refund=refund).first()
    source = (
        {"refund_attempt": attempt}
        if attempt is not None
        else {"refund": refund}
    )
    credited = (
        attempt is not None
        and RetailCredit.objects.filter(refund_attempt=attempt).exists()
    ) or RetailCredit.objects.filter(refund=refund).exists()
    if credited:
        return
    scale(max(Decimal(refund.amount.amount) - reversed_amount, _ZERO), **source)


__all__ = [
    "ChargeKind",
    "Piece",
    "allocate_decision",
    "allocate_pro_rata",
    "allocation_of",
    "allocation_of_refund",
    "book_refund",
    "ensure_allocations",
    "free_reversed",
    "has_rates",
    "headroom",
    "headroom_total",
    "mark_moved",
    "pieces_of",
    "scale",
    "split_over_shares",
    "store",
]
