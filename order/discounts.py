"""Proportional allocation of order-level discounts across items.

Invoices and the AADE myDATA submission are line-based: an order-level
monetary discount (promotions + loyalty) must reduce the per-line gross
values so VAT recomputes on what the customer actually paid, and the
rounded lines must still sum EXACTLY to the discounted total (myDATA
errors 203 / 207-210 otherwise). The largest-remainder method
guarantees that.

Gift-card amounts are deliberately NOT allocated — a gift card is a
payment instrument (multi-purpose voucher), so it settles the invoice
without reducing its taxable value.
"""

from __future__ import annotations

from decimal import ROUND_FLOOR, Decimal

CENT = Decimal("0.01")


def order_discount_total(order) -> Decimal:
    total = Decimal(0)
    for money in (order.discount_amount, order.loyalty_discount):
        if money and money.amount > 0:
            total += Decimal(money.amount)
    return total.quantize(CENT)


def discounted_line_gross(order, items=None) -> dict[int, Decimal]:
    """Map ``OrderItem.pk`` → line gross after discount allocation.

    ``items`` lets callers pass an already-fetched list so the invoice
    builder and the VAT breakdown iterate the same rows they render.
    """
    if items is None:
        items = list(order.items.all())

    gross: dict[int, Decimal] = {
        item.pk: Decimal(item.price.amount) * Decimal(item.quantity)
        for item in items
    }
    items_total = sum(gross.values(), Decimal(0))
    discount = order_discount_total(order)
    if discount <= 0 or items_total <= 0:
        return gross

    discount = min(discount, items_total)

    shares: dict[int, Decimal] = {}
    remainders: dict[int, Decimal] = {}
    allocated = Decimal(0)
    for pk, line in gross.items():
        raw = discount * line / items_total
        floored = raw.quantize(CENT, rounding=ROUND_FLOOR)
        shares[pk] = floored
        remainders[pk] = raw - floored
        allocated += floored

    leftover_cents = int(((discount - allocated) / CENT).to_integral_value())
    for pk, _remainder in sorted(
        remainders.items(), key=lambda kv: (-kv[1], kv[0])
    )[:leftover_cents]:
        shares[pk] += CENT

    # A remainder cent can, in the razor's-edge case where the discount
    # nearly equals the items total, push one line past its own gross.
    # Repair by moving the overflow to the largest remaining line so
    # no line goes negative and the total allocation stays exact.
    for pk in gross:
        overflow = shares[pk] - gross[pk]
        if overflow > 0:
            shares[pk] = gross[pk]
            for other_pk, _line in sorted(
                gross.items(), key=lambda kv: -(kv[1] - shares[kv[0]])
            ):
                if other_pk == pk:
                    continue
                headroom = gross[other_pk] - shares[other_pk]
                if headroom <= 0:
                    continue
                moved = min(overflow, headroom)
                shares[other_pk] += moved
                overflow -= moved
                if overflow <= 0:
                    break

    return {pk: gross[pk] - shares[pk] for pk in gross}
