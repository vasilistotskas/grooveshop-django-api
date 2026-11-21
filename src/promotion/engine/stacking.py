"""Which monetary promotions combine, and how each one's money is
allocated over the cart lines it covers."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from djmoney.money import Money

from order.checkout.discounts import allocate_capped
from promotion.engine.bxgy import bxgy_discounted_units
from promotion.engine.results import (
    AppliedPromotion,
    CartDiscountResult,
    quantize,
)
from promotion.engine.scope import matching_items
from promotion.enum import BenefitType, CouponRejectionReason
from promotion.models import Promotion


def resolve_stacking(
    monetary: list[AppliedPromotion],
    result: CartDiscountResult,
) -> list[AppliedPromotion]:
    """Stackables combine; the best non-stackable applies alone —
    whichever set discounts more wins."""
    stackables = sorted(
        (e for e in monetary if e.promotion.stackable),
        key=lambda e: (e.promotion.priority, e.promotion.pk),
    )
    non_stackables = [e for e in monetary if not e.promotion.stackable]

    stack_total = sum((e.amount.amount for e in stackables), Decimal(0))
    best_single = max(
        non_stackables, key=lambda e: e.amount.amount, default=None
    )

    if best_single is None:
        return stackables
    if not stackables or best_single.amount.amount >= stack_total:
        chosen = [best_single]
        losers = stackables + [
            e for e in non_stackables if e is not best_single
        ]
    else:
        chosen = stackables
        losers = non_stackables

    for loser in losers:
        if loser.code is not None:
            result.rejected.append(
                (
                    loser.code.code,
                    str(CouponRejectionReason.COMBINATION_DISALLOWED),
                )
            )
    return chosen


def clamp(
    chosen: list[AppliedPromotion],
    cart_items,
    currency: str,
) -> list[AppliedPromotion]:
    """Allocate each promotion to the lines it covers, never below zero.

    A promotion's money is split over the cart lines it covers
    (``_covered_weights``) by ``allocate_capped``, the one allocation
    rule: largest remainder to the cent, and no line gets more than
    is left of its value after the promotions before it. A promotion
    worth more than its covered lines still hold is cut down to what
    they hold; one the earlier promotions left nothing to act on ends
    at 0 (``evaluate`` refuses its code).

    Returns NEW entries rather than mutating the ones handed in:
    the coupon picker clamps the same baseline entries once per
    candidate coupon, and an in-place rewrite would carry each
    pass's clamping into the next one.
    """
    remaining = {
        item.pk: Decimal(str(item.total_price.amount)) for item in cart_items
    }
    clamped: list[AppliedPromotion] = []
    for entry in chosen:
        shares = allocate_capped(
            quantize(entry.amount.amount),
            _covered_weights(entry.promotion, cart_items),
            remaining,
        )
        for pk, share in shares.items():
            remaining[pk] -= share
        clamped.append(
            AppliedPromotion(
                entry.promotion,
                entry.code,
                Money(sum(shares.values(), Decimal(0)), currency),
                {pk: share for pk, share in shares.items() if share > 0},
            )
        )
    return clamped


def _covered_weights(promotion: Promotion, cart_items) -> dict[int, Decimal]:
    """The cart lines a monetary promotion discounts, by what they weigh.

    Cart item pk -> the value the promotion's discount is spread by:
    the lines ``matching_items`` counts, by their value, for a
    percentage or a fixed amount; the units a buy-X-get-Y actually
    discounts, by their price. A line outside the promotion's scope or
    past its exclusions is not in the result, so it never carries the
    discount, and neither does the VAT rate it may sit at.
    """
    weights: dict[int, Decimal] = defaultdict(Decimal)
    if promotion.benefit_type == BenefitType.BXGY:
        for unit_price, pk in bxgy_discounted_units(promotion, cart_items):
            weights[pk] += unit_price
    else:
        for item in matching_items(promotion, cart_items):
            weights[item.pk] += Decimal(str(item.total_price.amount))
    return {pk: weight for pk, weight in weights.items() if weight > 0}
