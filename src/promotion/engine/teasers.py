"""Near-miss teasers: what an automatic promotion still needs, and the
cart its advice would build."""

from __future__ import annotations

from decimal import ROUND_CEILING, Decimal

from django.conf import settings
from djmoney.money import Money

from promotion.engine.results import (
    NEW_LINE_PK,
    NearMissEntry,
    SimulatedLine,
    quantize,
)
from promotion.engine.scope import matching_items
from promotion.enum import CouponRejectionReason
from promotion.models import Promotion


def teaser_cost(entry: NearMissEntry, cart_items) -> Decimal | None:
    """What following ``entry`` adds to the cart, in money.

    The value of the lines ``items_after_teaser`` adds — the
    same simulation ``PromotionEngine._teaser_holds`` checks the promise on — so
    a units teaser ("2 more items") can be weighed against an amount
    one. ``None`` when there is nothing to build on.
    """
    future_items = items_after_teaser(entry, cart_items)
    if future_items is None:
        return None
    return sum(
        (item.total_price.amount for item in future_items), Decimal(0)
    ) - sum((item.total_price.amount for item in cart_items), Decimal(0))


def items_after_teaser(entry: NearMissEntry, cart_items) -> list | None:
    """The cart lines once the teaser's advice is followed.

    Units go onto the cheapest line the promotion counts — the
    conservative reading, since a buy-X-get-Y discounts its cheapest
    units — and a missing reward arrives as one new line of its
    cheapest sellable product. ``None`` when there is nothing to
    build on.
    """
    if entry.reward_products:
        reward = min(
            entry.reward_products,
            key=lambda product: product.final_price.amount,
        )
        return [
            *cart_items,
            SimulatedLine(
                pk=NEW_LINE_PK,
                product=reward,
                quantity=entry.remaining_quantity or 1,
                final_price=Money(
                    reward.final_price.amount, settings.DEFAULT_CURRENCY
                ),
            ),
        ]

    matching = matching_items(entry.promotion, cart_items)
    if not matching:
        return None
    target = min(matching, key=lambda item: item.final_price.amount)
    if entry.remaining_quantity:
        extra = entry.remaining_quantity
    else:
        unit = target.final_price.amount
        if unit <= 0 or entry.remaining_amount is None:
            return None
        extra = int(
            (entry.remaining_amount / unit).to_integral_value(
                rounding=ROUND_CEILING
            )
        )
    return [
        SimulatedLine(
            pk=item.pk,
            product=item.product,
            quantity=item.quantity + extra,
            final_price=item.final_price,
        )
        if item is target
        else item
        for item in cart_items
    ]


def worthless_code_reason(shortfall: NearMissEntry | None) -> str:
    """Why a code that would take nothing off this cart is refused.

    Short of a buy-X-get-Y group (or its reward) is a minimum the
    shopper can still meet; anything else — e.g. a reward-pool offer
    whose rewards cannot be bought — leaves nothing in the cart for
    the code to act on.
    """
    if shortfall is not None:
        return str(CouponRejectionReason.MINIMUM_NOT_MET)
    return str(CouponRejectionReason.NOT_APPLICABLE)


def minimum_near_miss(
    promotion: Promotion, cart_items, items_total: Decimal
) -> NearMissEntry | None:
    """The one missing minimum of an automatic promotion, if just one.

    Subtotal and quantity are separate distances; when both are
    unmet there is no single "add X more" to tell the shopper, so
    nothing is emitted. The strictly-exceed subtotal distance never
    reports 0 for a cart resting exactly on the threshold (adveshop
    near-miss design note).
    """
    amount_short = Decimal(0)
    if promotion.min_subtotal:
        amount_short = quantize(
            Decimal(str(promotion.min_subtotal.amount)) - items_total
        )
    units_short = 0
    if promotion.min_quantity:
        units = sum(
            item.quantity for item in matching_items(promotion, cart_items)
        )
        units_short = promotion.min_quantity - units

    if amount_short > 0 and units_short <= 0:
        return NearMissEntry(promotion, remaining_amount=amount_short)
    if units_short > 0 and amount_short <= 0:
        return NearMissEntry(promotion, remaining_quantity=units_short)
    return None


def bxgy_shortfall(promotion: Promotion, cart_items) -> NearMissEntry | None:
    """What a buy-X-get-Y still needs before it earns once, if anything.

    Mirrors ``bxgy_amount``'s two modes. A same-pool offer needs
    ``buy + get`` eligible units ("buy 2 get 1 free" needs 3). A
    reward-pool offer needs ``buy`` units on the buy side, then one
    of its reward products in the cart — reported with
    ``reward_products`` so the shopper is told WHAT to add (demo
    store: a case in the cart said nothing about its "-50% screen
    protector"). ``None`` when the cart holds none of the eligible
    units (nothing to build on), the group is already complete, or
    no reward product can be bought.
    """
    buy_qty = promotion.buy_quantity or 0
    get_qty = promotion.get_quantity or 0
    if buy_qty < 1 or get_qty < 1:
        return None
    units = sum(item.quantity for item in matching_items(promotion, cart_items))
    if units == 0:
        return None
    rewards = list(promotion.get_products.all())
    if not rewards:
        short = buy_qty + get_qty - units
        if short <= 0:
            return None
        return NearMissEntry(promotion, remaining_quantity=short)
    if units < buy_qty:
        return NearMissEntry(promotion, remaining_quantity=buy_qty - units)
    reward_ids = {product.pk for product in rewards}
    if any(item.product_id in reward_ids for item in cart_items):
        return None
    sellable = sorted(
        (p for p in rewards if p.active and p.stock > 0),
        key=lambda product: product.pk,
    )
    if not sellable:
        return None
    # One reward unit already earns the offer (``bxgy_amount``
    # discounts whatever reward units are present, up to the cap).
    return NearMissEntry(
        promotion, remaining_quantity=1, reward_products=sellable
    )
