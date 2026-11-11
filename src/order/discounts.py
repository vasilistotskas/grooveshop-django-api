"""Per-line allocation of order-level discounts, snapshotted at checkout.

Invoices and the AADE myDATA submission are line-based: a monetary
discount (promotions + loyalty) must reduce the gross value of the lines
it was actually granted on, so VAT is owed on what the customer paid at
each rate, and the rounded lines must still sum EXACTLY to the discounted
total (myDATA errors 203 / 207-210 otherwise).

Where a discount lands is decided once, when the order is created:

* a promotion lands on the lines it covers (``PromotionEngine`` reports
  them, ``CartDiscountResult.line_discounts``): a coupon for one category
  never reduces a line of another, which is the line that may carry a
  different VAT rate;
* an order-wide amount (the loyalty redemption) is spread over every line
  by the value that is left on it.

The result is frozen on each ``OrderItem.discount_allocation``, exactly
as ``OrderItem.vat_rate`` freezes the rate. The tax documents read that
snapshot (``discounted_line_gross``) and never re-derive it, so editing a
promotion or a product afterwards cannot rewrite a document already
earned. Every split uses the same largest-remainder rule
(``proportional_shares``), and the cart preview, the payment-intent amount
and order creation all get their allocation from the one function,
``allocate_capped``.

Gift-card amounts are deliberately NOT allocated — a gift card is a
payment instrument (multi-purpose voucher), so it settles the invoice
without reducing its taxable value.
"""

from __future__ import annotations

from collections.abc import Hashable, Mapping, Sequence
from decimal import ROUND_FLOOR, Decimal
from typing import Any

from djmoney.money import Money

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


def allocate_capped[K: Hashable](
    amount: Decimal,
    weights: Mapping[K, Decimal],
    caps: Mapping[K, Decimal],
) -> dict[K, Decimal]:
    """Split ``amount`` over the keys in proportion to ``weights``, giving
    no key more than its ``caps`` entry.

    The single allocation rule behind every per-line discount: a promotion
    passes the lines it covers weighted by their value, the loyalty
    redemption passes every line weighted by what is left on it, and the
    cap is what is left on the line. The shares sum EXACTLY to ``amount``
    (largest remainder, to the cent), or to the caps' total when ``amount``
    is more than the keys can take. A key whose proportional share would
    pass its cap is pinned at the cap and the rest goes to the others, so
    no line is ever pushed below zero. Keys without a positive weight or
    headroom take nothing; the result holds every key of ``weights``.
    """
    shares: dict[K, Decimal] = {key: Decimal(0) for key in weights}
    open_weights = {
        key: weight
        for key, weight in weights.items()
        if weight > 0 and caps[key] > 0
    }
    remaining = min(
        amount, sum((caps[key] for key in open_weights), Decimal(0))
    )
    while remaining > 0 and open_weights:
        split = proportional_shares(remaining, open_weights)
        over = [key for key in open_weights if split[key] > caps[key]]
        if not over:
            for key, share in split.items():
                shares[key] += share
            break
        for key in over:
            shares[key] = caps[key]
            remaining -= caps[key]
            del open_weights[key]
    return shares


def spread_by_remaining_value(
    items: Sequence[Any], amount: Decimal
) -> dict[int, Decimal]:
    """``OrderItem.pk`` → its share of an order-wide ``amount``.

    Spread by the value still on each line (gross less what is already
    allocated to it), so a promotion that took a line down leaves the
    line proportionally less to carry the rest.
    """
    remaining = {
        item.pk: Decimal(item.total_price.amount)
        - Decimal(item.discount_allocation.amount)
        for item in items
    }
    return allocate_capped(amount, remaining, remaining)


def add_line_discounts(
    items: Sequence[Any], discounts: Mapping[int, Decimal]
) -> None:
    """Add ``discounts`` (``OrderItem.pk`` → amount) to the lines' snapshots."""
    from order.models.item import OrderItem

    changed = []
    for item in items:
        extra = discounts.get(item.pk, Decimal(0))
        if extra:
            item.discount_allocation = Money(
                item.discount_allocation.amount + extra,
                item.discount_allocation.currency,
            )
            changed.append(item)
    OrderItem.objects.bulk_update(
        changed, ["discount_allocation", "discount_allocation_currency"]
    )


def discounted_line_gross(order, items=None) -> dict[int, Decimal]:
    """Map ``OrderItem.pk`` → line gross after its snapshotted discount.

    ``items`` lets callers pass an already-fetched list so the invoice
    builder and the VAT breakdown iterate the same rows they render.

    The snapshots must add up to the discount the order carries (capped at
    the goods it can reduce): a document built on lines that do not would
    bill a total the customer never owed. That only happens for an order
    whose snapshot was never written (created by the release before the
    column existed, until ``heal_discount_allocation`` runs) or whose
    discounted lines staff deleted, and a wrong tax document is worse than
    one that waits for a person, so it raises instead of guessing.
    """
    if items is None:
        items = list(order.items.all())

    gross = {
        item.pk: Decimal(item.price.amount) * Decimal(item.quantity)
        for item in items
    }
    allocated = sum(
        (Decimal(item.discount_allocation.amount) for item in items),
        Decimal(0),
    )
    expected = min(order_discount_total(order), sum(gross.values(), Decimal(0)))
    if allocated != expected:
        raise ValueError(
            f"Order {order.pk} carries a discount of {expected} but its "
            f"lines' discount snapshots add up to {allocated}, so no "
            "per-line VAT can be derived. Run "
            "`manage.py heal_discount_allocation` for an order created "
            "during a rollout; otherwise a person must reconcile it."
        )
    return {
        item.pk: gross[item.pk] - Decimal(item.discount_allocation.amount)
        for item in items
    }
