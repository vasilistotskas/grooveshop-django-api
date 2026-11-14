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
  made: a bare amount pro rata over the headroom, a refund by lines (see
  :mod:`order.refund_lines`) as its own pieces, each at its line's rate,
  which must fit their rates (:func:`first_short_rate`). Every other source (a refund nobody claimed, a returned chargeback)
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

from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from django.db.models import Exists, F, OuterRef, Q, QuerySet, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.translation import gettext
from djmoney.money import Money

from order.checkout.discounts import allocate_capped, proportional_shares
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
    #: Of a refund by lines, the part of the line's value that went back as
    #: loyalty points: no room and no credit, but value the line has had
    #: back. ``amount`` is the part that is room.
    points: Decimal = _ZERO


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
    order: Order,
    *,
    settled_only: bool = False,
    released: Collection[int] = (),
) -> dict[Decimal, Decimal]:
    """The gross each VAT rate can still be credited. See the module doc.
    With *settled_only* the claims still PENDING hold nothing: the room
    there would be once each is resolved without a refund. *released* is
    the same for the named attempts alone."""
    left, invoice = _base(order)
    counting = CreditAllocation.objects.counting().filter(order=order)
    if settled_only:
        counting = counting.exclude(
            refund_attempt__status=RefundAttemptStatus.PENDING
        )
    if released:
        counting = counting.exclude(refund_attempt__in=released)
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
    order: Order,
    amount: Decimal,
    *,
    settled_only: bool = False,
    released: Collection[int] = (),
) -> dict[Decimal, Decimal]:
    """*amount* spread over the headroom, capped to it; the result sums to
    less than *amount* when the headroom cannot hold it all."""
    left = {
        rate: gross
        for rate, gross in headroom(
            order, settled_only=settled_only, released=released
        ).items()
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


def goods_returned(
    order: Order, *, before: PaymentTransaction | None = None
) -> Decimal:
    """The goods the provider's money has paid back, as it stood at a cut:
    what every refund that moved before the ledger row *before* (``None``:
    every one that moved) returned of goods (``goods_claimed``: what it
    owed after the duplicate payment), less what the provider reported
    failed of it by the same cut. A reversal booked at or after the cut is
    not yet known there, however late the refund is decided. Gift card and
    points returns are not the provider's money; a chargeback is no part of
    it (``PaymentLedger.excess_held`` takes off only what a lost dispute
    took of the duplicate payment itself: a dispute on the sale is sale
    money lost, whichever way staff decide its goods). A refund's
    duplicate-payment part owes no credit and is no goods: the order is
    still for what the ledger holds less this, so the excess is what is
    above it (``PaymentLedger.excess_held``)."""
    rows = (
        CreditAllocation.objects.filter(
            order=order,
            moved_at__isnull=False,
            goods_claimed__gt=0,
            decision__isnull=True,
        )
        .exclude(refund_attempt__kind=RefundAttemptKind.STORED_VALUE)
        .select_related("refund", "refund_attempt__refund")
    )
    if before is not None:
        rows = rows.filter(moved_at__lt=before.created_at)
    sources = [(row, row.refund or row.refund_attempt.refund) for row in rows]
    if not sources:
        return _ZERO
    reversals = PaymentTransaction.objects.filter(
        kind=PaymentTransactionKind.REFUND_REVERSAL,
        provider_reference__in={
            refund.provider_reference for _row, refund in sources if refund
        },
    )
    if before is not None:
        reversals = reversals.filter(
            Q(created_at__lt=before.created_at)
            | Q(created_at=before.created_at, pk__lt=before.pk)
        )
    failed: dict[tuple[str, str], Decimal] = {}
    for reversal in reversals:
        key = (reversal.provider, reversal.provider_reference)
        failed[key] = failed.get(key, _ZERO) + Decimal(reversal.amount.amount)
    total = _ZERO
    for row, refund in sources:
        paid = Decimal((refund or row.refund_attempt).amount.amount)
        if refund:
            paid -= failed.get(
                (refund.provider, refund.provider_reference), _ZERO
            )
        total += max(min(row.goods_claimed, paid), _ZERO)
    return total


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


def scale_pieces(pieces: Sequence[Piece], total: Decimal) -> list[Piece]:
    """*pieces* cut down to *total* of room in proportion, to the cent, each
    keeping its line, kind and rate. What is cut off is not lost: it becomes
    the piece's ``points``, value the line has had back that owes no credit
    (a piece left with no room at all stays, as points only). A *total* that
    is not less than their sum leaves them as they are."""
    held = sum((p.amount for p in pieces), _ZERO)
    if total >= held:
        return list(pieces)
    shares = (
        proportional_shares(
            max(total, _ZERO),
            {i: piece.amount for i, piece in enumerate(pieces)},
        )
        if total > 0
        else {i: _ZERO for i in range(len(pieces))}
    )
    return [
        Piece(
            kind=piece.kind,
            rate=piece.rate,
            amount=shares[i],
            item_pk=piece.item_pk,
            quantity=piece.quantity,
            points=piece.points + piece.amount - shares[i],
        )
        for i, piece in enumerate(pieces)
    ]


def by_rate(pieces: Iterable[Piece]) -> dict[Decimal, Decimal]:
    """The gross of *pieces* per VAT rate."""
    out: dict[Decimal, Decimal] = {}
    for piece in pieces:
        out[piece.rate] = out.get(piece.rate, _ZERO) + piece.amount
    return out


def first_short_rate(
    order: Order,
    wanted: Mapping[Decimal, Decimal],
    *,
    settled_only: bool = False,
    released: Collection[int] = (),
) -> tuple[Decimal, Decimal] | None:
    """The first VAT rate (highest first) whose headroom cannot hold the
    gross *wanted* at it, with what it can hold; ``None`` when every rate
    fits. A refund by lines needs each of its rates to fit: its credit
    document must carry exactly them. *settled_only* and *released* are
    :func:`headroom`'s: the room there would be with the claims still
    PENDING (or the named ones) resolved without a refund."""
    left = headroom(order, settled_only=settled_only, released=released)
    for rate in sorted(wanted, reverse=True):
        room = max(left.get(rate, _ZERO), _ZERO)
        if wanted[rate] > room:
            return rate, room
    return None


def store(
    order: Order,
    pieces: Sequence[Piece],
    *,
    moved_at: datetime | None = None,
    nothing_owed: bool = False,
    uncovered: Decimal = _ZERO,
    goods: Decimal | None = None,
    **source,
) -> None:
    """Write *pieces* as the allocation of one source (``refund_attempt=``,
    ``refund=`` or ``decision=``). *nothing_owed* says the source owes no
    credit at all: it is marked allocated by a row of amount zero.
    *uncovered* is the part the receipt had no room for, kept on the
    source's first row so the store can be told (:func:`uncovered_orders`).
    *goods* is what the source returned of goods before any reversal
    (``goods_claimed``, also on the first row): the pieces and the
    uncovered part when not given."""
    currency = order.amount_due.currency
    if goods is None:
        goods = sum((p.amount for p in pieces), _ZERO) + uncovered
    if nothing_owed:
        CreditAllocation.objects.create(
            order=order,
            rate=_ZERO,
            amount=Money(_ZERO, currency),
            moved_at=moved_at,
            uncovered=uncovered,
            goods_claimed=goods,
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
            points=piece.points,
            moved_at=moved_at,
            uncovered=uncovered if i == 0 else _ZERO,
            goods_claimed=goods if i == 0 else _ZERO,
            **source,
        )
        for i, piece in enumerate(
            p for p in pieces if p.amount > 0 or p.points > 0
        )
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
    keeps one row of amount zero: it was decided, and owes nothing. What no
    credit could cover (``uncovered``) follows what stands: at most what
    stands less what the rows hold now, never negative."""
    rows = list(CreditAllocation.objects.filter(**source).order_by("pk"))
    claimed = sum((Decimal(r.amount.amount) for r in rows), _ZERO)
    if not rows:
        return
    uncovered = sum((r.uncovered for r in rows), _ZERO)
    goods = sum((r.goods_claimed for r in rows), _ZERO)
    if returned < claimed:
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
            elif row.points > 0:
                # No room left, but the line keeps the value it had back.
                row.amount = Money(_ZERO, row.amount.currency)
                row.save(update_fields=["amount", "amount_currency"])
            else:
                row.delete()
        # The goods the source claimed stay on its first row left.
        left = CreditAllocation.objects.filter(**source).order_by("pk")
        first_left = left.first()
        if first_left is None:
            first = rows[0]
            store(
                first.order,
                [],
                moved_at=first.moved_at,
                nothing_owed=True,
                goods=goods,
                **source,
            )
            return
        left.update(goods_claimed=_ZERO)
        CreditAllocation.objects.filter(pk=first_left.pk).update(
            goods_claimed=goods
        )
    if uncovered <= 0:
        return
    held = min(returned, claimed)
    stands = min(uncovered, max(returned - held, _ZERO))
    kept = CreditAllocation.objects.filter(**source).order_by("pk")
    kept.update(uncovered=_ZERO)
    first_kept = kept.first()
    if first_kept is not None and stands > 0:
        CreditAllocation.objects.filter(pk=first_kept.pk).update(
            uncovered=stands
        )


def scale_points(returned: Decimal, **source) -> None:
    """Make the points a source's rows carry total *returned* (less than
    they do), in proportion: what was claimed as points and not returned is
    not held back for its lines any more. A row left with neither room nor
    points is deleted; a source left with no row at all keeps one row of
    amount zero, so that it stays decided and owes nothing."""
    rows = list(
        CreditAllocation.objects.filter(points__gt=0, **source).order_by("pk")
    )
    claimed = sum((r.points for r in rows), _ZERO)
    if not rows or returned >= claimed:
        return
    shares = (
        proportional_shares(returned, {r.pk: r.points for r in rows})
        if returned > 0
        else {}
    )
    for row in rows:
        CreditAllocation.objects.filter(pk=row.pk).update(
            points=shares.get(row.pk, _ZERO)
        )
    for row in CreditAllocation.objects.filter(
        amount=0, points=0, **source
    ).exclude(item__isnull=True):
        row.delete()
    if not CreditAllocation.objects.filter(**source).exists():
        first = rows[0]
        store(
            first.order,
            [],
            moved_at=first.moved_at,
            nothing_owed=True,
            goods=_ZERO,
            **source,
        )


def allocate_source(
    order: Order,
    amount: Decimal,
    *,
    moved_at: datetime | None,
    excess: Decimal = _ZERO,
    excess_if_claims_fail: Decimal | None = None,
    reversed_: Decimal = _ZERO,
    **source,
) -> bool:
    """Allocate a source that was booked without a claim (a provider's
    webhook refund, a returned chargeback), pro rata over the headroom now.
    The one writer for money nobody claimed; :func:`allocate_order` is its
    one caller.

    *excess* of *amount* gives back a duplicate payment
    (``PaymentLedger.refundable_excess``) and owes nothing. *amount* is what
    was booked; *reversed_* is what the provider reported failed of it. A
    reversal comes off the duplicate part first, exactly as
    :func:`scale` does for a source decided before it was reversed, so the
    source owes the same whenever it is decided: its goods part, at most
    what stands.

    What a source owes depends on two things a claim still PENDING holds
    back: the room on the receipt and the duplicate-payment excess. Both
    follow ONE rule. What settled sources and issued credits took is final:
    the source is cut to what fits (a source that owes nothing, or whose
    receipt has no taxable room, gets one row of amount zero), the part no
    credit can cover is kept on the row (``uncovered``) and the order's
    history says so ONCE, here. But when the PENDING claims decide the
    outcome (more room, or more excess, if they were all released) the
    source gets NO row, which says "not decided yet", and the call returns
    ``False``; it is decided once those claims resolve. *excess_if_claims_fail*
    is the excess with no claim holding any; only the part of it this
    source can take counts, so a claim that holds excess the source would
    not take anyway never makes it wait. Returns whether the source was
    decided.
    """
    if excess_if_claims_fail is not None and min(
        excess_if_claims_fail, amount
    ) > min(excess, amount):
        return False
    goods = amount - min(excess, amount)
    owed = min(goods, amount - reversed_)
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
    uncovered = owed - held if taxable and held < owed else _ZERO
    store(
        order,
        pieces,
        moved_at=moved_at,
        nothing_owed=not pieces,
        uncovered=uncovered,
        goods=goods if taxable else _ZERO,
        **source,
    )
    if uncovered > 0:
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
    reversed_: Decimal = _ZERO


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
        failed = reversed_of(refund)
        if Decimal(refund.amount.amount) - failed > 0:
            out.append(
                Owing(
                    refund.order_id,
                    refund.created_at,
                    "r",
                    {"refund": refund},
                    Decimal(refund.amount.amount),
                    before=refund,
                    reversed_=failed,
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
        amount = Decimal((refund or attempt).amount.amount)
        failed = reversed_of(refund) if refund else _ZERO
        if amount - failed > 0:
            out.append(
                Owing(
                    attempt.order_id,
                    refund.created_at if refund else None,
                    "a",
                    {"refund_attempt": attempt},
                    amount,
                    before=refund,
                    exclude=(attempt.pk,),
                    reversed_=failed,
                )
            )
    # A gift card return booked without an allocation (the previous
    # release serving after the migration): what it put back onto cards is
    # payment the receipt holds. One that moved before an existing receipt
    # is netted by it and owes none, like every other source.
    from order.payment_ledger import PaymentLedger

    receipts = dict(
        Invoice.objects.filter(**scope).values_list("order_id", "created_at")
    )
    for attempt in RefundAttempt.objects.filter(
        kind=RefundAttemptKind.STORED_VALUE,
        status=RefundAttemptStatus.SETTLED,
        credit_document__isnull=True,
        allocations__isnull=True,
        **scope,
    ):
        amount = PaymentLedger.stored_gift_card_returned_by(attempt)
        moment = PaymentLedger.stored_returned_at(attempt)
        receipt_at = receipts.get(attempt.order_id)
        if amount <= 0 or (
            receipt_at is not None and (moment is None or moment < receipt_at)
        ):
            continue
        out.append(
            Owing(
                attempt.order_id,
                moment,
                "a",
                {"refund_attempt": attempt},
                amount,
                has_excess=False,
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


def allocate_order(order: Order) -> int:
    """Decide every source of *order* that has no allocation yet, oldest
    first, each over what the ones before it left. The caller holds the
    order's row lock, and the sources are read inside it.

    The write paths that read allocations call this first (the receipt, a
    new claim, the credit documents, a booked refund; a chargeback decision
    calls it once it has changed its decisions), so the allocation never
    depends on which of them ran first: what a release that did not know
    about allocations booked, or what a failure left, is allocated before
    anything else reads or takes the room. A read path (a specimen render,
    the admin) never calls it. Returns how many sources were decided. A
    write path issues the credits itself (or its caller does, once the money
    is booked); the periodic task asks for them
    (``order.credit_allocation_backfill``), so a source decided on an order
    that already has a receipt is credited then.

    Sources are decided strictly oldest first, and the first one that
    cannot be decided yet (:func:`allocate_source`: a claim still PENDING
    holds the room or the excess it needs) stops the run: a younger source
    never takes room an older one waits for, and the order of decisions is
    the order of the money, whatever the order the claims resolve in. The
    cost is that a younger source waits too, until the claim resolves and
    the next run decides both.
    """
    from order.payment_ledger import PaymentLedger

    done = 0
    pending = sorted(
        owing_sources(order),
        key=lambda o: (o.moment is None, o.moment, o.rank),
    )
    for owing in pending:
        excess = max_excess = _ZERO
        if owing.has_excess:
            excess = PaymentLedger.refundable_excess(
                order, before=owing.before, exclude=owing.exclude
            )
            max_excess = PaymentLedger.refundable_excess(
                order,
                before=owing.before,
                exclude=owing.exclude,
                claims_hold=False,
            )
        if not allocate_source(
            order,
            owing.amount,
            moved_at=owing.moment,
            excess=excess,
            excess_if_claims_fail=max_excess,
            reversed_=owing.reversed_,
            **owing.source,
        ):
            break
        done += 1
    if done:
        # What the order is still for moved with the goods just decided.
        newest = (
            PaymentTransaction.objects.filter(order=order)
            .order_by("created_at", "pk")
            .last()
        )
        if newest is not None:
            PaymentLedger.sync_overcharge(order, newest)
    return done


def undecided(order: Order) -> bool:
    """Whether *order* still has a source that owes a credit and has no
    allocation. After :func:`allocate_order` ran, that is exactly a source
    waiting for a claim still PENDING: nothing may rely on the order's
    books being complete (a receipt, the settlement credit, a new claim)
    until it resolves."""
    return bool(owing_sources(order))


def uncovered_orders() -> QuerySet[CreditAllocation]:
    """The allocations of sources whose books need a person: money that
    moved but that the receipt had no room to credit (``uncovered``: refunds
    past what the receipt holds), or a receipt left short by a refund the
    provider later reversed (``understated``). Each is for the store to look
    at until staff acknowledge it (:func:`acknowledge_uncovered`)."""
    return beyond_receipt_orders() | understated_orders()


def beyond_receipt_orders() -> QuerySet[CreditAllocation]:
    """The rows among :func:`uncovered_orders` whose money moved past what
    the receipt could credit, not yet acknowledged."""
    return CreditAllocation.objects.filter(
        uncovered__gt=0, uncovered_acknowledged_at__isnull=True
    )


def understated_orders() -> QuerySet[CreditAllocation]:
    """The rows among :func:`uncovered_orders` that leave the receipt short
    of goods the customer kept (a refund netted from it was reversed)."""
    return CreditAllocation.objects.filter(
        understated__gt=0, understated_acknowledged_at__isnull=True
    )


def acknowledge_uncovered(order: Order, *, user) -> int:
    """Staff have seen the money past *order*'s receipt, or the receipt
    short of goods the customer kept, and settled it with the accountant:
    each alert clears on its own timestamp. Returns how many sources it
    cleared; the order's history records who did it."""
    now = timezone.now()
    over = beyond_receipt_orders().filter(order=order)
    short = understated_orders().filter(order=order)
    totals = {
        "uncovered": over.aggregate(total=Sum("uncovered"))["total"],
        "understated": short.aggregate(total=Sum("understated"))["total"],
    }
    count = len(
        {
            *over.values_list("pk", flat=True),
            *short.values_list("pk", flat=True),
        }
    )
    over.update(uncovered_acknowledged_at=now)
    short.update(understated_acknowledged_at=now)
    if count and totals["uncovered"]:
        OrderHistory.log_note(
            order=order,
            note=gettext(
                "%(amount)s refunded beyond the receipt was acknowledged "
                "as handled outside the credit documents by %(who)s."
            )
            % {"amount": totals["uncovered"], "who": user},
        )
    if count and totals["understated"]:
        OrderHistory.log_note(
            order=order,
            note=gettext(
                "%(amount)s of goods the receipt is short of, after a "
                "refund was reversed, was acknowledged as corrected "
                "outside the receipt by %(who)s."
            )
            % {"amount": totals["understated"], "who": user},
        )
    return count


def _blocks(order: Order, owing: Owing, claim: RefundAttempt) -> bool:
    """Whether *claim* (PENDING) keeps *owing* undecided: whether releasing
    it alone, with no refund, would change what the source owes."""
    from order.payment_ledger import PaymentLedger

    if claim.pk in owing.exclude:
        return False
    excess = _ZERO
    if owing.has_excess:
        excess = PaymentLedger.refundable_excess(
            order, before=owing.before, exclude=owing.exclude
        )
        freed = PaymentLedger.refundable_excess(
            order,
            before=owing.before,
            exclude=(*owing.exclude, claim.pk),
        )
        if min(freed, owing.amount) > min(excess, owing.amount):
            return True
    owed = min(
        owing.amount - min(excess, owing.amount),
        owing.amount - owing.reversed_,
    )
    if owed <= 0 or not has_rates(order):
        return False
    return sum(
        allocate_pro_rata(order, owed, released=(claim.pk,)).values(), _ZERO
    ) > sum(allocate_pro_rata(order, owed).values(), _ZERO)


def room_blockers(
    order: Order,
    short: Callable[..., bool],
    *,
    exclude: Collection[int] = (),
) -> list[RefundAttempt]:
    """The claims still PENDING that a refund short of room waits for, or
    ``[]`` when it is a final refusal (it would not fit with every one of
    them released either). *short* says whether the refund is still short
    given :func:`headroom`'s ``settled_only`` / ``released`` keywords. A
    claim whose release alone makes it fit is named; when only several
    together do, every claim holding room is."""
    claims = list(
        RefundAttempt.objects.live()
        .filter(order=order)
        .exclude(pk__in=exclude)
        .order_by("pk")
    )
    if not claims or short(settled_only=True):
        return []
    alone = [c for c in claims if not short(released=(c.pk,))]
    if alone:
        return alone
    held = set(
        CreditAllocation.objects.counting()
        .filter(order=order, refund_attempt__in=claims)
        .values_list("refund_attempt", flat=True)
    )
    return [c for c in claims if c.pk in held]


def blocking_claims(order: Order) -> list[RefundAttempt]:
    """The refund claims still PENDING that keep a source of *order*
    undecided: a receipt deferred, a new refund refused. A claim that holds
    nothing a waiting source needs is not one."""
    waiting = owing_sources(order)
    if not waiting:
        return []
    return [
        claim
        for claim in RefundAttempt.objects.live()
        .filter(order=order)
        .order_by("pk")
        if any(_blocks(order, owing, claim) for owing in waiting)
    ]


def excess_holders(
    order: Order, amount: Decimal, *, exclude: Collection[int] = ()
) -> list[RefundAttempt]:
    """The PENDING provider claims that hold part of the order's duplicate
    payment that a refund of *amount* claimed now would take: releasing
    one alone would leave it more excess."""
    from order.payment_ledger import PaymentLedger

    held = min(PaymentLedger.refundable_excess(order, exclude=exclude), amount)
    return [
        claim
        for claim in RefundAttempt.objects.live()
        .filter(order=order, kind=RefundAttemptKind.PROVIDER)
        .exclude(pk__in=exclude)
        .order_by("pk")
        if min(
            PaymentLedger.refundable_excess(
                order, exclude=(*exclude, claim.pk)
            ),
            amount,
        )
        > held
    ]


def describe_blockers(
    order: Order, claims: Sequence[RefundAttempt] | None = None
) -> str:
    """The claims that block, in words, for a message to staff (*claims*, or
    those that keep a source of the order undecided)."""
    parts = [
        gettext("the %(kind)s refund of %(amount)s")
        % {
            "kind": (
                gettext("gift card and points")
                if claim.kind == RefundAttemptKind.STORED_VALUE
                else gettext("payment provider")
            ),
            "amount": claim.amount,
        }
        for claim in (blocking_claims(order) if claims is None else claims)
    ]
    return ", ".join(parts)


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
    sent to AADE, and the order is told so an accountant can look at it.
    A refund the receipt netted (its money moved before the receipt) has no
    credit to cancel: the receipt itself is then short of the goods the
    customer kept, and the order and the store dashboard say so
    (:func:`understated_orders`)."""
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
    before = _goods_of(**source)
    first = CreditAllocation.objects.filter(**source).order_by("pk").first()
    # Always scaled, a credit or not: the goods that came back are what the
    # provider really paid (``goods_returned``). A credit already issued
    # holds its room by its own document, not by this allocation.
    scale(
        max(Decimal(refund.amount.amount) - reversed_of(refund), _ZERO),
        **source,
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
    invoice = Invoice.objects.filter(order=refund.order).first()
    if (
        invoice is None
        or first is None
        or first.moved_at is None
        or first.moved_at >= invoice.created_at
    ):
        return
    freed = before - _goods_of(**source)
    if freed <= 0:
        return
    CreditAllocation.objects.filter(
        pk=CreditAllocation.objects.filter(**source)
        .order_by("pk")
        .values("pk")[:1]
    ).update(
        understated=F("understated") + freed,
        understated_acknowledged_at=None,
    )
    OrderHistory.log_note(
        order=refund.order,
        note=gettext(
            "The provider reported refund %(reference)s failed, but the "
            "receipt had already been issued net of it: the receipt is "
            "%(amount)s short of the goods the customer kept. An "
            "accountant has to correct it."
        )
        % {"reference": refund.provider_reference, "amount": freed},
    )


def _goods_of(**source) -> Decimal:
    """What one source holds of goods: its allocation and the part no
    credit could cover."""
    totals = CreditAllocation.objects.filter(**source).aggregate(
        amount=Coalesce(Sum("amount"), _ZERO),
        uncovered=Coalesce(Sum("uncovered"), _ZERO),
    )
    return Decimal(totals["amount"]) + Decimal(totals["uncovered"])


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
    "acknowledge_uncovered",
    "allocate_order",
    "allocate_pro_rata",
    "allocate_source",
    "allocation_of",
    "allocation_of_refund",
    "beyond_receipt_orders",
    "blocking_claims",
    "by_rate",
    "describe_blockers",
    "drop",
    "excess_holders",
    "first_short_rate",
    "free_reversed",
    "goods_returned",
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
    "room_blockers",
    "scale",
    "scale_pieces",
    "scale_points",
    "split_over_shares",
    "store",
    "uncovered_orders",
    "undecided",
    "understated_orders",
]
