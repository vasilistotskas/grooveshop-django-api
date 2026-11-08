"""The VAT split of every credit, decided when the money is claimed.

See :mod:`order.models.credit_allocation` for the rule. This module is the
one writer of ``CreditAllocation`` rows and the one reader of the room that
is left on the receipt:

* :func:`headroom` is a plain sum: the receipt's gross per rate (the order's
  VAT lines while there is no receipt), less the allocations of every source
  that was not declined and whose money moves at or after the receipt (an
  in-flight claim included), less the credit documents themselves. A source
  that has a credit is counted by the document's own VAT rows, never by its
  allocation.
* :func:`allocate_pro_rata` spreads an amount over that headroom, to the
  cent, capped to it; a claim that does not fit is refused by its caller
  before any money moves.
* Every source is allocated when it is created, so nothing here is ever
  allocated by a read: a refund claim by ``OrderService._claim_refund``, a
  refund nobody claimed (a provider's webhook) by :func:`book_refund` over
  the headroom of that moment, a returned chargeback by
  :func:`allocate_decision`. Money booked before allocations existed was
  allocated by migration ``0103`` (``order.credit_allocation_backfill``).
* A provider's reversal of a refund scales its allocation down
  (:func:`free_reversed`) unless a credit document was already issued.

Points owe no credit (they are a discount, the receipt does not hold their
value), so a refund's points share is never allocated; a gift card is
payment and is.

Everything here runs under the order's row lock (the callers hold it).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from django.db.models import Exists, OuterRef, Q, QuerySet, Sum
from django.db.models.functions import Coalesce
from django.utils.translation import gettext
from djmoney.money import Money

from order.discounts import allocate_capped, proportional_shares
from order.enum.payment_transaction import PaymentTransactionKind
from order.models import (
    ChargebackDecision,
    CreditAllocation,
    Invoice,
    Order,
    OrderHistory,
    PaymentTransaction,
    RetailCredit,
)
from order.models.invoice import MyDataStatus
from order.models.refund_attempt import (
    RefundAttempt,
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


def _credited(issued: QuerySet[RetailCredit]) -> Q:
    """Allocation rows whose source has one of the *issued* credits."""
    return (
        Exists(issued.filter(refund_attempt=OuterRef("refund_attempt")))
        | Exists(issued.filter(refund=OuterRef("refund")))
        | Exists(issued.filter(refund=OuterRef("refund_attempt__refund")))
        | Exists(issued.filter(chargeback=OuterRef("decision__chargeback")))
    )


def headroom(order: Order) -> dict[Decimal, Decimal]:
    """The gross each VAT rate can still be credited. See the module doc."""
    left, invoice = _base(order)
    counting = CreditAllocation.objects.counting().filter(order=order)
    issued = RetailCredit.objects.none()
    if invoice is not None:
        issued = RetailCredit.objects.filter(invoice=invoice).exclude(
            mydata_status=MyDataStatus.CANCELED
        )
        # What a credit holds is read off the document itself; the
        # allocation it was issued from has done its work.
        counting = counting.filter(
            Q(moved_at__isnull=True) | Q(moved_at__gte=invoice.created_at)
        ).exclude(_credited(issued))
    for row in counting:
        left[row.rate] = left.get(row.rate, _ZERO) - Decimal(row.amount.amount)
    for credit in issued:
        for row in credit.vat_breakdown:
            rate = Decimal(row["rate"])
            left[rate] = left.get(rate, _ZERO) - Decimal(row["gross"])
    return left


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


def reversed_of(refund: PaymentTransaction) -> Decimal:
    """What the provider reported failed of a booked refund, in all."""
    return PaymentTransaction.objects.filter(
        provider=refund.provider,
        provider_reference=refund.provider_reference,
        kind=PaymentTransactionKind.REFUND_REVERSAL,
    ).aggregate(total=Coalesce(Sum("amount"), _ZERO))["total"]


def reversed_credits() -> QuerySet[RetailCredit]:
    """The credit documents of refunds a provider later reported failed:
    each stands (it records what was sent to AADE) until an accountant
    cancels it, so each is for someone to look at."""
    reversals = PaymentTransaction.objects.filter(
        kind=PaymentTransactionKind.REFUND_REVERSAL,
        provider=OuterRef("refund__provider"),
        provider_reference=OuterRef("refund__provider_reference"),
    )
    return (
        RetailCredit.objects.filter(refund__isnull=False)
        .exclude(mydata_status=MyDataStatus.CANCELED)
        .filter(Exists(reversals))
        .order_by("pk")
    )


def free_reversed(refund: PaymentTransaction) -> None:
    """A provider reported a booked refund failed (in whole or in part):
    the money was never paid back, so its room on the receipt is free
    again, scaled by everything reversed so far. A credit document that
    was issued for the refund already stays, as the record of what was
    sent to AADE, and the order is told so an accountant can look at it."""
    attempt = RefundAttempt.objects.filter(refund=refund).first()
    source = (
        {"refund_attempt": attempt}
        if attempt is not None
        else {"refund": refund}
    )
    credit = (
        RetailCredit.objects.filter(refund=refund)
        .exclude(mydata_status=MyDataStatus.CANCELED)
        .first()
    )
    if credit is not None:
        OrderHistory.log_note(
            order=refund.order,
            note=gettext(
                "The provider reported refund %(reference)s failed after "
                "its credit document %(number)s was issued. The document "
                "stands until an accountant cancels it."
            )
            % {
                "reference": refund.provider_reference,
                "number": credit.credit_number,
            },
        )
        return
    scale(
        max(Decimal(refund.amount.amount) - reversed_of(refund), _ZERO),
        **source,
    )


def link_attempt(attempt: RefundAttempt, refund: PaymentTransaction) -> None:
    """The ledger refund *refund* answers *attempt*: the attempt's own
    allocation is the refund's (scaled to what really moved), so the
    allocation the refund got when it was booked unclaimed is dropped."""
    CreditAllocation.objects.filter(refund=refund).delete()
    scale(Decimal(refund.amount.amount), refund_attempt=attempt)
    mark_moved(refund.created_at, refund_attempt=attempt)


__all__ = [
    "ChargeKind",
    "Piece",
    "allocate_decision",
    "allocate_pro_rata",
    "allocation_of",
    "allocation_of_refund",
    "book_refund",
    "free_reversed",
    "has_rates",
    "headroom",
    "headroom_total",
    "link_attempt",
    "mark_moved",
    "pieces_of",
    "reversed_credits",
    "reversed_of",
    "scale",
    "split_over_shares",
    "store",
]
