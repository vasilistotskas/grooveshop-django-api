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

from collections.abc import Hashable
from decimal import ROUND_FLOOR, Decimal

CENT = Decimal("0.01")


def order_discount_total(order) -> Decimal:
    total = Decimal(0)
    for money in (order.discount_amount, order.loyalty_discount):
        if money and money.amount > 0:
            total += Decimal(money.amount)
    return total.quantize(CENT)


def proportional_shares[K: Hashable](
    amount: Decimal, weights: dict[K, Decimal]
) -> dict[K, Decimal]:
    """Split ``amount`` over ``weights`` so the shares sum EXACTLY to it.

    Each share is floored to the cent and the leftover cents go to the
    largest remainders (ties broken by key), the same largest-remainder
    rule for every split the invoice, the myDATA payload and the cart
    share. ``weights`` must total more than zero.
    """
    total = sum(weights.values(), Decimal(0))

    shares: dict[K, Decimal] = {}
    remainders: dict[K, Decimal] = {}
    allocated = Decimal(0)
    for key, weight in weights.items():
        raw = amount * weight / total
        floored = raw.quantize(CENT, rounding=ROUND_FLOOR)
        shares[key] = floored
        remainders[key] = raw - floored
        allocated += floored

    leftover_cents = int(((amount - allocated) / CENT).to_integral_value())
    for key, _remainder in sorted(
        remainders.items(), key=lambda kv: (-kv[1], kv[0])
    )[:leftover_cents]:
        shares[key] += CENT
    return shares


def allocate_discount[K: Hashable](
    gross: dict[K, Decimal], discount: Decimal
) -> dict[K, Decimal]:
    """Reduce each line gross by its proportional share of ``discount``.

    The discount is capped at the lines' total, shares are floored to the
    cent and the leftover cents go to the largest remainders, so the
    returned lines sum EXACTLY to ``total - discount``. Callers pass a
    mapping of any hashable line key to its gross value.
    """
    items_total = sum(gross.values(), Decimal(0))
    if discount <= 0 or items_total <= 0:
        return gross

    discount = min(discount, items_total)
    shares = proportional_shares(discount, gross)

    # A remainder cent can, in the razor's-edge case where the discount
    # nearly equals the items total, push one line past its own gross.
    # Repair by moving the overflow to the largest remaining line so
    # no line goes negative and the total allocation stays exact.
    for key, gross_share in gross.items():
        overflow = shares[key] - gross_share
        if overflow > 0:
            shares[key] = gross_share
            for other, _line in sorted(
                gross.items(), key=lambda kv: -(kv[1] - shares[kv[0]])
            ):
                if other == key:
                    continue
                headroom = gross[other] - shares[other]
                if headroom <= 0:
                    continue
                moved = min(overflow, headroom)
                shares[other] += moved
                overflow -= moved
                if overflow <= 0:
                    break

    return {key: gross[key] - shares[key] for key in gross}


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
    return allocate_discount(gross, order_discount_total(order))
