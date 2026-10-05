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

from collections import defaultdict
from collections.abc import Hashable, Iterable
from decimal import ROUND_FLOOR, Decimal

CENT = Decimal("0.01")


def order_discount_total(order) -> Decimal:
    total = Decimal(0)
    for money in (order.discount_amount, order.loyalty_discount):
        if money and money.amount > 0:
            total += Decimal(money.amount)
    return total.quantize(CENT)


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

    shares: dict[K, Decimal] = {}
    remainders: dict[K, Decimal] = {}
    allocated = Decimal(0)
    for key, line in gross.items():
        raw = discount * line / items_total
        floored = raw.quantize(CENT, rounding=ROUND_FLOOR)
        shares[key] = floored
        remainders[key] = raw - floored
        allocated += floored

    leftover_cents = int(((discount - allocated) / CENT).to_integral_value())
    for key, _remainder in sorted(
        remainders.items(), key=lambda kv: (-kv[1], kv[0])
    )[:leftover_cents]:
        shares[key] += CENT

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


def vat_buckets(
    lines: Iterable[tuple[Decimal, Decimal]],
) -> dict[Decimal, dict[str, Decimal]]:
    """Group ``(gross, rate)`` lines by VAT rate, backing VAT out of gross.

    Line prices are VAT-inclusive, so ``subtotal = gross / (1 + rate/100)``.
    Each bucket is quantized to the cent AFTER summing its lines — the one
    rounding rule the invoice, the myDATA payload and the cart share, so
    their VAT figures agree.
    """
    raw: dict[Decimal, dict[str, Decimal]] = defaultdict(
        lambda: {
            "subtotal": Decimal(0),
            "vat": Decimal(0),
            "gross": Decimal(0),
        }
    )
    for gross, rate in lines:
        divisor = Decimal(1) + rate / Decimal(100)
        subtotal = gross / divisor if divisor else gross
        bucket = raw[rate]
        bucket["subtotal"] += subtotal
        bucket["vat"] += gross - subtotal
        bucket["gross"] += gross
    return {
        rate: {name: value.quantize(CENT) for name, value in bucket.items()}
        for rate, bucket in raw.items()
    }
