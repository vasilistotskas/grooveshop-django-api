"""Buy-X-get-Y: the units an offer discounts, and what they are worth."""

from __future__ import annotations

from decimal import Decimal

from djmoney.money import Money

from promotion.engine.results import quantize
from promotion.engine.scope import matching_items
from promotion.models import Promotion


def _unit_prices(items) -> list[tuple[Decimal, int]]:
    """Expand cart lines to one ``(unit price, cart item pk)`` per unit.

    Reads the CartItem property, not ``product.final_price``, so a
    wholesale-bound cart credits BxGY discounts against the price
    the buyer actually pays — retail carts resolve identically.
    """
    units: list[tuple[Decimal, int]] = []
    for item in items:
        unit = Decimal(str(item.final_price.amount))
        units.extend([(unit, item.pk)] * item.quantity)
    return units


def bxgy_discounted_units(
    promotion: Promotion, cart_items
) -> list[tuple[Decimal, int]]:
    """The ``(unit price, cart item pk)`` units a buy-X-get-Y discounts.

    Two deterministic modes (documented on the admin form):

    - ``get_products`` EMPTY — same-pool: every group of
      ``buy_quantity + get_quantity`` eligible units earns one
      application; the CHEAPEST ``get_quantity`` units per
      application are discounted by ``get_discount_percent``.
      ("Buy 2 get 1 free" needs 3 units in the cart.)
    - ``get_products`` SET — reward-pool: applications =
      ``floor(eligible buy units / buy_quantity)``; the cheapest
      reward units already in the cart (up to ``applications ×
      get_quantity``) are discounted. Reward units that also match
      the buy scope still count on the buy side — the rule stays
      monotonic as the shopper adds items.

    "Cheapest" ties break by cart item pk, so every evaluation of the
    same cart (preview, quote, order creation) discounts the
    same lines. Empty while the promotion earns nothing.
    """
    buy_qty = promotion.buy_quantity or 0
    get_qty = promotion.get_quantity or 0
    if buy_qty < 1 or get_qty < 1:
        return []

    buy_units = _unit_prices(matching_items(promotion, cart_items))
    reward_ids = {p.id for p in promotion.get_products.all()}

    if not reward_ids:
        applications = len(buy_units) // (buy_qty + get_qty)
        candidates = buy_units
    else:
        applications = len(buy_units) // buy_qty
        candidates = _unit_prices(
            [item for item in cart_items if item.product_id in reward_ids]
        )
    if applications < 1:
        return []
    return sorted(candidates)[: applications * get_qty]


def bxgy_amount(promotion: Promotion, cart_items, currency: str) -> Money:
    """Buy-X-get-Y-discounted, on units already in the cart."""
    discounted_units = bxgy_discounted_units(promotion, cart_items)
    amount = sum((price for price, _pk in discounted_units), Decimal(0))
    amount *= promotion.get_discount_percent / Decimal(100)
    if promotion.max_discount_amount:
        amount = min(amount, Decimal(str(promotion.max_discount_amount.amount)))
    return Money(quantize(amount), currency)
