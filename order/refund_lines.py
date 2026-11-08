"""A refund by lines: what chosen order lines (and the shipping) are worth,
at the figures the order froze, split by VAT rate.

A goods line is worth its ``price`` x ``quantity`` less its snapshotted
``discount_allocation`` (``order.discounts.discounted_line_gross``), at its
snapshotted ``vat_rate``: the same number the receipt was built from, so a
credit of it lands on the rate the receipt holds it under. Units are priced
cumulatively (the value of the first *k* units is the line's value x k / n,
to the cent, the last unit taking what is left), so refunding a line in
several steps adds up to exactly the line and never a cent more.

Shipping is split over the rates the way the receipt splits it
(``order.taxation.order_vat_lines``: value apportionment, largest
remainder), one piece per rate.

:func:`plan_pieces` returns the *pieces* (one per goods line or per
shipping rate); :func:`split_over_attempts` lays them over the shares a
refund is paid in (the provider's charges first, then gift cards and
points), cutting a piece where a share ends.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Sum
from django.utils.translation import gettext_lazy as _

from order.discounts import CENT, discounted_line_gross
from order.exceptions import PaymentError
from order.models import Order, OrderItem
from order.models.refund_attempt import RefundAttemptLine
from order.taxation import ChargeKind, order_vat_lines


@dataclass(frozen=True)
class RefundPiece:
    """A part of a refund that belongs to one VAT rate."""

    kind: ChargeKind
    rate: Decimal
    amount: Decimal
    item_pk: int | None = None
    quantity: int = 0


def refunded_quantities(order: Order) -> dict[int, int]:
    """Order line pk to the units already paid back by line refunds (the
    shares that hold their money), not the units put back on the shelf
    (``OrderItem.refunded_quantity``)."""
    return {
        row["item"]: row["units"]
        for row in RefundAttemptLine.objects.holding()
        .filter(attempt__order=order, item__isnull=False)
        .values("item")
        .annotate(units=Sum("quantity"))
    }


def shipping_refunded(order: Order) -> bool:
    """Whether a line refund already paid the shipping back."""
    return (
        RefundAttemptLine.objects.holding()
        .filter(attempt__order=order, kind=ChargeKind.SHIPPING)
        .exists()
    )


def _units_value(gross: Decimal, quantity: int, units: int) -> Decimal:
    """The value of the first ``units`` of ``quantity`` units worth
    ``gross`` together; the last unit takes the remainder."""
    if units >= quantity:
        return gross
    return (gross * units / quantity).quantize(CENT, rounding=ROUND_HALF_UP)


def plan_pieces(
    order: Order,
    requested: Mapping[int, int],
    *,
    include_shipping: bool,
    lock: bool = False,
) -> list[RefundPiece]:
    """The pieces of a refund of ``requested`` (order line pk to units) and,
    when asked, the shipping.

    With ``lock`` (a refund being claimed; the caller holds the order lock)
    the lines are locked in pk order; without it the plan is a quote, for a
    dialog to show.
    Raises :class:`~order.exceptions.PaymentError` for a line that is not
    the order's, more units than were sold less those already paid back, a
    shipping already paid back or not charged, or a refund of nothing.
    """
    lines = OrderItem.objects.filter(order=order).order_by("pk")
    if lock:
        lines = lines.select_for_update(of=("self",))
    rows = list(lines)
    by_pk = {item.pk: item for item in rows}
    already = refunded_quantities(order)
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
        paid = already.get(pk, 0)
        if paid + units > item.quantity:
            raise PaymentError(
                _(
                    "%(name)s: %(requested)s unit(s) cannot be refunded, "
                    "%(sold)s were sold and %(paid)s already paid back."
                )
                % {
                    "name": item.product_name,
                    "requested": units,
                    "sold": item.quantity,
                    "paid": paid,
                }
            )
        value = _units_value(
            gross[pk], item.quantity, paid + units
        ) - _units_value(gross[pk], item.quantity, paid)
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
        if shipping_refunded(order):
            raise PaymentError(
                _("The shipping of this order was already refunded.")
            )
        shipping = [
            line
            for line in order_vat_lines(order, rows)
            if line.kind == ChargeKind.SHIPPING
        ]
        if not shipping:
            raise PaymentError(_("This order has no shipping to refund."))
        pieces.extend(
            RefundPiece(
                kind=ChargeKind.SHIPPING, rate=line.rate, amount=line.gross
            )
            for line in shipping
        )

    if not pieces:
        raise PaymentError(
            _(
                "The lines chosen are worth nothing, so there is nothing to refund."
            )
        )
    return pieces


def split_over_attempts(
    pieces: Sequence[RefundPiece], shares: Sequence[Decimal]
) -> list[list[RefundPiece]]:
    """Lay ``pieces`` over ``shares`` in order. The two must total the same.

    A piece cut by the end of a share becomes two, the units staying with
    the first."""
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
                quantity=0,
            )
            left = Decimal(0)
        out.append(take)
    return out
