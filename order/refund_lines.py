"""A refund by lines: what chosen order lines (and the shipping) are worth,
at the figures the order froze, split by VAT rate.

A goods line is worth its ``price`` x ``quantity`` less its snapshotted
``discount_allocation`` (``order.discounts.discounted_line_gross``), at its
snapshotted ``vat_rate``: the same number the receipt was built from, so a
credit of it lands on the rate the receipt holds it under. Units are priced
cumulatively (the value of the first *k* units is the line's value x k / n,
to the cent, the last unit taking what is left), so refunding a line in
several steps adds up to exactly the line and never a cent more.

**What a line has had back is measured in VALUE, never in units.** A line
split over two refund shares (two charges, or a card and a gift card) can
have one share paid and the other not; the value its holding shares carry
(settled, or pending and not abandoned) against its discounted gross is
what is left, and the units still on offer are the ones that value has not
covered. Shipping is measured the same way, per VAT rate.

Shipping is split over the rates the way the receipt splits it
(``order.taxation.order_vat_lines``: value apportionment, largest
remainder), one piece per rate.

:func:`plan_pieces` returns the *pieces* (one per goods line or per
shipping rate); :func:`split_over_attempts` lays them over the shares a
refund is paid in (the provider's charges first, then gift cards and
points), cutting a piece where a share ends.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from django.utils.translation import gettext_lazy as _

from order.discounts import CENT, discounted_line_gross
from order.exceptions import PaymentError
from order.models import Order, OrderItem
from order.models.refund_attempt import (
    RefundAttempt,
    RefundAttemptLine,
    RefundAttemptStatus,
)
from order.taxation import ChargeKind, order_vat_lines


@dataclass(frozen=True)
class RefundPiece:
    """A part of a refund that belongs to one VAT rate.

    ``quantity`` is the units the refund asked for on the line; a piece cut
    over two shares carries it on each part. It identifies the request (a
    retry compares it) and is never summed: what a line has had back is
    its value.
    """

    kind: ChargeKind
    rate: Decimal
    amount: Decimal
    item_pk: int | None = None
    quantity: int = 0


@dataclass(frozen=True)
class HeldValues:
    """What the holding shares of an order's line refunds carry."""

    goods: Mapping[int, Decimal]
    """Order line pk to the gross held."""
    shipping: Mapping[Decimal, Decimal]
    """VAT rate to the shipping gross held."""

    def shipping_total(self) -> Decimal:
        return sum(self.shipping.values(), Decimal(0))


def held_values(order: Order) -> HeldValues:
    """The value each line, and the shipping, has had back from refund
    shares that hold their money (settled, or pending and not abandoned)."""
    goods: dict[int, Decimal] = defaultdict(Decimal)
    shipping: dict[Decimal, Decimal] = defaultdict(Decimal)
    for row in RefundAttemptLine.objects.holding().filter(attempt__order=order):
        if row.kind == ChargeKind.SHIPPING:
            shipping[row.rate] += Decimal(row.amount.amount)
        elif row.item_id is not None:
            goods[row.item_id] += Decimal(row.amount.amount)
    return HeldValues(goods=dict(goods), shipping=dict(shipping))


def _units_value(gross: Decimal, quantity: int, units: int) -> Decimal:
    """The value of the first ``units`` of ``quantity`` units worth
    ``gross`` together; the last unit takes the remainder."""
    if units >= quantity:
        return gross
    return (gross * units / quantity).quantize(CENT, rounding=ROUND_HALF_UP)


def covered_units(gross: Decimal, quantity: int, held: Decimal) -> int:
    """How many whole units of a line worth ``gross`` the value ``held``
    has paid for. A line worth nothing (a free gift) pays for none: it can
    always be returned, and refunds no money."""
    if gross <= 0:
        return 0
    covered = 0
    while (
        covered < quantity
        and _units_value(gross, quantity, covered + 1) <= held
    ):
        covered += 1
    return covered


def plan_pieces(
    order: Order,
    requested: Mapping[int, int],
    *,
    include_shipping: bool,
    lock: bool = False,
    allow_empty: bool = False,
) -> list[RefundPiece]:
    """The pieces of a refund of ``requested`` (order line pk to units) and,
    when asked, the shipping that has not been refunded yet.

    With ``lock`` (a refund being claimed; the caller holds the order lock)
    the lines are locked in pk order; without it the plan is a quote, for a
    dialog to show. A line's piece is the value of the units asked for
    beyond those its held value covers, less the part of the next unit the
    held value already pays.

    Raises :class:`~order.exceptions.PaymentError` for a line that is not
    the order's, more units than the line has left, a shipping with
    nothing left to refund or not charged, or a refund of nothing (not
    with ``allow_empty``: lines worth nothing, returned for the shelf only).
    """
    lines = OrderItem.objects.filter(order=order).order_by("pk")
    if lock:
        lines = lines.select_for_update(of=("self",))
    rows = list(lines)
    by_pk = {item.pk: item for item in rows}
    held = held_values(order)
    try:
        gross = discounted_line_gross(order, rows)
    except ValueError as exc:
        raise PaymentError(str(exc)) from exc

    pieces: list[RefundPiece] = []
    for pk in sorted(requested):
        units = requested[pk]
        item = by_pk.get(pk)
        if item is None:
            raise PaymentError(_("A line to refund is not on this order."))
        if units < 1:
            raise PaymentError(_("Refund at least one unit of a line."))
        paid = held.goods.get(pk, Decimal(0))
        done = covered_units(gross[pk], item.quantity, paid)
        if done + units > item.quantity:
            raise PaymentError(
                _(
                    "%(name)s: %(requested)s unit(s) cannot be refunded, "
                    "%(sold)s were sold and %(paid)s already paid back."
                )
                % {
                    "name": item.product_name,
                    "requested": units,
                    "sold": item.quantity,
                    "paid": done,
                }
            )
        value = _units_value(gross[pk], item.quantity, done + units) - paid
        if value > 0:
            pieces.append(
                RefundPiece(
                    kind=ChargeKind.GOODS,
                    rate=Decimal(item.vat_rate),
                    amount=value,
                    item_pk=pk,
                    quantity=units,
                )
            )

    if include_shipping:
        shipping = [
            line
            for line in order_vat_lines(order, rows)
            if line.kind == ChargeKind.SHIPPING
        ]
        if not shipping:
            raise PaymentError(_("This order has no shipping to refund."))
        left = [
            (line.rate, line.gross - held.shipping.get(line.rate, Decimal(0)))
            for line in shipping
        ]
        left = [(rate, amount) for rate, amount in left if amount > 0]
        if not left:
            raise PaymentError(
                _("The shipping of this order was already refunded.")
            )
        pieces.extend(
            RefundPiece(kind=ChargeKind.SHIPPING, rate=rate, amount=amount)
            for rate, amount in left
        )

    if not pieces and not allow_empty:
        raise PaymentError(
            _(
                "The lines chosen are worth nothing, so there is nothing to "
                "refund."
            )
        )
    return pieces


def split_over_attempts(
    pieces: Sequence[RefundPiece], shares: Sequence[Decimal]
) -> list[list[RefundPiece]]:
    """Lay ``pieces`` over ``shares`` in order. The two must total the same.

    A piece cut by the end of a share becomes two parts of the same line
    and rate, each carrying the piece's ``quantity`` (the request) and its
    own value."""
    remaining = list(pieces)
    out: list[list[RefundPiece]] = []
    for share in shares:
        take: list[RefundPiece] = []
        left = share
        while left > 0 and remaining:
            piece = remaining[0]
            if piece.amount <= left:
                take.append(piece)
                left -= piece.amount
                remaining.pop(0)
                continue
            take.append(
                RefundPiece(
                    kind=piece.kind,
                    rate=piece.rate,
                    amount=left,
                    item_pk=piece.item_pk,
                    quantity=piece.quantity,
                )
            )
            remaining[0] = RefundPiece(
                kind=piece.kind,
                rate=piece.rate,
                amount=piece.amount - left,
                item_pk=piece.item_pk,
                quantity=piece.quantity,
            )
            left = Decimal(0)
        out.append(take)
    return out


@dataclass(frozen=True)
class LineRequest:
    """What a refund by lines asked for: units per order line pk and
    whether the shipping was part of it."""

    units: Mapping[int, int]
    include_shipping: bool


def request_of(rows: Iterable[RefundAttemptLine]) -> LineRequest:
    """The request the rows of one refund (all its shares) were made for."""
    units: dict[int, int] = {}
    shipping = False
    for row in rows:
        if row.kind == ChargeKind.SHIPPING:
            shipping = True
        elif row.item_id is not None:
            units[row.item_id] = max(units.get(row.item_id, 0), row.quantity)
    return LineRequest(units=units, include_shipping=shipping)


@dataclass(frozen=True)
class PendingRequest:
    """A refund by lines whose provider call has an unknown outcome."""

    request_id: object
    request: LineRequest
    amount: Decimal
    shares: tuple[RefundAttempt, ...]


def request_groups(order: Order) -> dict[object, list[RefundAttempt]]:
    """Every refund-by-lines request of the order (all statuses), by its
    ``request_id``, shares oldest first."""
    groups: dict[object, list[RefundAttempt]] = defaultdict(list)
    for attempt in RefundAttempt.objects.filter(
        order=order, request_id__isnull=False
    ).order_by("created_at", "id"):
        groups[attempt.request_id].append(attempt)
    return groups


def pending_request(order: Order) -> PendingRequest | None:
    """The newest refund by lines that still has a live PENDING share (a
    provider call whose outcome is unknown), for the dialog to offer a
    retry of it under the same idempotency keys."""
    live_ids = set(
        RefundAttempt.objects.live()
        .filter(order=order, request_id__isnull=False)
        .values_list("request_id", flat=True)
    )
    best: PendingRequest | None = None
    for request_id, shares in request_groups(order).items():
        if request_id not in live_ids:
            continue
        rows = RefundAttemptLine.objects.filter(attempt__in=shares)
        best = PendingRequest(
            request_id=request_id,
            request=request_of(rows),
            amount=sum(
                (
                    Decimal(share.amount.amount)
                    for share in shares
                    if share.status != RefundAttemptStatus.DECLINED
                ),
                Decimal(0),
            ),
            shares=tuple(shares),
        )
    return best
