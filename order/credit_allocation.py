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
* A refund claim is allocated by ``OrderService._claim_refund`` when it is
  made. Every other source (a refund nobody claimed, a returned chargeback)
  is decided by :func:`allocate_order`, which every WRITE path calls first
  (the receipt, a claim, the credit documents, a booked refund, a decision)
  and the periodic task ``order.tasks.allocate_credit_sources`` runs for
  the rest; it reads the order's sources inside the order lock and decides
  them oldest first through :func:`allocate_source`. A read never allocates.
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

from django.db.models import Exists, F, OuterRef, Q, QuerySet, Sum
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


def _credited(issued: QuerySet[RetailCredit]) -> Q:
    """Allocation rows whose source has one of the *issued* credits."""
    return (
        Exists(issued.filter(refund_attempt=OuterRef("refund_attempt")))
        | Exists(issued.filter(refund=OuterRef("refund")))
        | Exists(issued.filter(refund=OuterRef("refund_attempt__refund")))
        | Exists(issued.filter(chargeback=OuterRef("decision__chargeback")))
    )


def headroom(
    order: Order, *, settled_only: bool = False
) -> dict[Decimal, Decimal]:
    """The gross each VAT rate can still be credited. See the module doc.
    With *settled_only* the claims still PENDING hold nothing: the room
    there would be once each is resolved without a refund."""
    left, invoice = _base(order)
    counting = CreditAllocation.objects.counting().filter(order=order)
    if settled_only:
        counting = counting.exclude(
            refund_attempt__status=RefundAttemptStatus.PENDING
        )
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


def allocate_pro_rata(
    order: Order, amount: Decimal, *, settled_only: bool = False
) -> dict[Decimal, Decimal]:
    """*amount* spread over the headroom, capped to it; the result sums to
    less than *amount* when the headroom cannot hold it all."""
    left = {
        rate: gross
        for rate, gross in headroom(order, settled_only=settled_only).items()
        if gross > 0
    }
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
    nothing_owed: bool = False,
    **source,
) -> None:
    """Write *pieces* as the allocation of one source (``refund_attempt=``,
    ``refund=`` or ``decision=``). *nothing_owed* says the source owes no
    credit at all: it is marked allocated by a row of amount zero."""
    currency = order.amount_due.currency
    if nothing_owed:
        CreditAllocation.objects.create(
            order=order,
            rate=_ZERO,
            amount=Money(_ZERO, currency),
            moved_at=moved_at,
            **source,
        )
        return
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
    for row in CreditAllocation.objects.filter(amount__gt=0, **source):
        out[row.rate] = out.get(row.rate, _ZERO) + Decimal(row.amount.amount)
    return out


def allocation_of_refund(refund: PaymentTransaction) -> dict[Decimal, Decimal]:
    """The allocation a booked refund's credit is issued from: its
    attempt's when an attempt claimed it, else its own."""
    attempt = RefundAttempt.objects.filter(refund=refund).first()
    if attempt is not None:
        return allocation_of(refund_attempt=attempt)
    return allocation_of(refund=refund)


def drop(**source) -> None:
    """Delete a source's allocation: it holds no room any more."""
    CreditAllocation.objects.filter(**source).delete()


def is_allocated(**source) -> bool:
    """Whether a source was allocated at all, even to nothing."""
    return CreditAllocation.objects.filter(**source).exists()


def is_refund_allocated(refund: PaymentTransaction) -> bool:
    """:func:`is_allocated` for the allocation a booked refund's credit is
    issued from (its attempt's when an attempt claimed it, else its own)."""
    attempt = RefundAttempt.objects.filter(refund=refund).first()
    if attempt is not None:
        return is_allocated(refund_attempt=attempt)
    return is_allocated(refund=refund)


def mark_moved(when: datetime, **source) -> None:
    CreditAllocation.objects.filter(**source).update(moved_at=when)


def scale(returned: Decimal, **source) -> None:
    """Make a source's rows total *returned* (less than they do): in
    proportion, rows left with nothing deleted. What was claimed and not
    returned is not held back any more. A source left with nothing at all
    keeps one row of amount zero: it was decided, and owes nothing."""
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
    if returned <= 0:
        first = rows[0]
        store(
            first.order,
            [],
            moved_at=first.moved_at,
            nothing_owed=True,
            **source,
        )


def allocate_source(
    order: Order,
    amount: Decimal,
    *,
    moved_at: datetime | None,
    excess: Decimal = _ZERO,
    **source,
) -> bool:
    """Allocate a source that was booked without a claim (a provider's
    webhook refund, a returned chargeback), pro rata over the headroom now.
    The one writer for money nobody claimed; :func:`allocate_order` is its
    one caller.

    *excess* of *amount* gives back a duplicate payment
    (``PaymentLedger.refundable_excess``) and owes nothing.

    There are two kinds of "no room". Room taken by settled sources and
    issued credits is final: the source is cut to what fits (a source that
    owes nothing, or whose receipt has no taxable room, gets one row of
    amount zero) and the order's history says so ONCE, here. Room held only
    by claims still PENDING is temporary: the source gets NO row, which
    says "not decided yet", and the call returns ``False``; it is decided
    once those claims resolve (``allocate_order`` runs again). Returns
    whether the source was decided.
    """
    owed = amount - min(excess, amount)
    taxable = has_rates(order)
    pieces: list[Piece] = []
    if owed > 0 and taxable:
        fit = allocate_pro_rata(order, owed)
        held = sum(fit.values(), _ZERO)
        if held < owed and (
            sum(allocate_pro_rata(order, owed, settled_only=True).values())
            > held
        ):
            return False
        pieces = pieces_of(fit)
    held = sum((p.amount for p in pieces), _ZERO)
    store(order, pieces, moved_at=moved_at, nothing_owed=not pieces, **source)
    if taxable and held < owed:
        OrderHistory.log_note(
            order=order,
            note=gettext(
                "The receipt holds %(held)s of the %(owed)s this refund or "
                "chargeback would credit: no credit document can be issued "
                "for the rest."
            )
            % {"held": held, "owed": owed},
        )
    return True


@dataclass(frozen=True)
class Owing:
    """A source of credit with no allocation. *before* is the ledger row the
    duplicate-payment excess is read before (``None``: now), *exclude* the
    attempts whose own claim is not held against it."""

    order_id: int
    moment: datetime | None
    rank: str
    source: dict
    amount: Decimal
    before: PaymentTransaction | None = None
    exclude: tuple[int, ...] = ()
    has_excess: bool = True


def owing_sources(order: Order | None = None) -> list[Owing]:
    """Every source that owes a credit and has no allocation (of one
    *order*, or of all), as the live models say. A source whose money moved
    before its order's receipt is netted by the receipt and owes none."""
    scope = {"order": order} if order is not None else {}
    out: list[Owing] = []
    for refund in PaymentTransaction.objects.filter(
        Q(order__invoice__isnull=True)
        | Q(created_at__gte=F("order__invoice__created_at")),
        kind=PaymentTransactionKind.REFUND,
        attempt__isnull=True,
        credit_document__isnull=True,
        allocations__isnull=True,
        **scope,
    ):
        amount = Decimal(refund.amount.amount) - reversed_of(refund)
        if amount > 0:
            out.append(
                Owing(
                    refund.order_id,
                    refund.created_at,
                    "r",
                    {"refund": refund},
                    amount,
                    before=refund,
                )
            )
    attempts = (
        RefundAttempt.objects.filter(
            # No refund yet: only a claim still in flight holds room. A
            # SETTLED attempt without one answered a refund another attempt
            # owns and holds nothing.
            Q(refund__isnull=True, status=RefundAttemptStatus.PENDING)
            | Q(
                refund__isnull=False,
                refund__credit_document__isnull=True,
            )
            & (
                Q(order__invoice__isnull=True)
                | Q(refund__created_at__gte=F("order__invoice__created_at"))
            ),
            kind=RefundAttemptKind.PROVIDER,
            credit_document__isnull=True,
            allocations__isnull=True,
            **scope,
        )
        .exclude(status=RefundAttemptStatus.DECLINED)
        .select_related("refund")
    )
    for attempt in attempts:
        refund = attempt.refund
        amount = (
            Decimal(refund.amount.amount) - reversed_of(refund)
            if refund
            else Decimal(attempt.amount.amount)
        )
        if amount > 0:
            out.append(
                Owing(
                    attempt.order_id,
                    refund.created_at if refund else None,
                    "a",
                    {"refund_attempt": attempt},
                    amount,
                    before=refund,
                    exclude=(attempt.pk,),
                )
            )
    decisions = ChargebackDecision.objects.filter(
        Q(chargeback__order__invoice__isnull=True)
        | Q(decided_at__gte=F("chargeback__order__invoice__created_at")),
        goods_returned=True,
        chargeback__chargeback_credit__isnull=True,
        allocations__isnull=True,
        **{f"chargeback__{key}": value for key, value in scope.items()},
    ).select_related("chargeback")
    for decision in decisions:
        out.append(
            Owing(
                decision.chargeback.order_id,
                decision.decided_at,
                "d",
                {"decision": decision},
                Decimal(decision.chargeback.amount.amount),
                has_excess=False,
            )
        )
    return out


def allocate_order(order: Order, *, request_credits: bool = False) -> int:
    """Decide every source of *order* that has no allocation yet, oldest
    first, each over what the ones before it left. The caller holds the
    order's row lock, and the sources are read inside it.

    Every WRITE path that reads allocations calls this first (the receipt,
    a claim, the credit documents, a booked refund, a decision), so the
    allocation never depends on which of them ran first: what a release that
    did not know about allocations booked, or what a failure left, is
    allocated before anything else reads or takes the room. A read path (a
    specimen render, the admin) never calls it. Returns how many sources
    were decided. A write path issues the credits itself (or its caller
    does, once the money is booked); the periodic task passes
    *request_credits*, so a source decided on an order that already has a
    receipt (one skipped for lack of an allocation) is credited now.
    """
    from order.payment_ledger import PaymentLedger

    done = 0
    pending = sorted(
        owing_sources(order),
        key=lambda o: (o.moment is None, o.moment, o.rank),
    )
    for owing in pending:
        excess = _ZERO
        if owing.has_excess:
            excess = PaymentLedger.refundable_excess(
                order, before=owing.before, exclude=owing.exclude
            )
        if allocate_source(
            order,
            owing.amount,
            moved_at=owing.moment,
            excess=excess,
            **owing.source,
        ):
            done += 1
    if (
        done
        and request_credits
        and Invoice.objects.filter(order=order).exists()
    ):
        from order.credit_documents import request_credit_documents

        request_credit_documents(order.pk)
    return done


def allocate_decision(order: Order, decision: ChargebackDecision) -> None:
    """The room a lost dispute's returned goods take on the receipt,
    decided now: the chargeback spread over the headroom, capped to it
    (:func:`allocate_order` decides the decision among the sources before
    and after it)."""
    CreditAllocation.objects.filter(decision=decision).delete()
    allocate_order(order)


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
    "Owing",
    "Piece",
    "allocate_decision",
    "allocate_order",
    "allocate_pro_rata",
    "allocate_source",
    "allocation_of",
    "allocation_of_refund",
    "drop",
    "free_reversed",
    "has_rates",
    "headroom",
    "headroom_total",
    "is_allocated",
    "is_refund_allocated",
    "link_attempt",
    "mark_moved",
    "owing_sources",
    "pieces_of",
    "reversed_credits",
    "reversed_of",
    "scale",
    "split_over_shares",
    "store",
]
