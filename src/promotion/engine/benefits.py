"""What one promotion is worth on a cart, and the gift a FREE_GIFT
promotion hands over."""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING

from djmoney.money import Money

from promotion.engine.bxgy import bxgy_amount
from promotion.engine.results import (
    CartDiscountResult,
    GiftEntitlement,
    quantize,
)
from promotion.engine.scope import matching_items
from promotion.enum import BenefitType
from promotion.models import Promotion

if TYPE_CHECKING:
    from product.models.product import Product

logger = logging.getLogger(__name__)


def amount_for(
    promotion: Promotion,
    cart_items,
    items_total: Decimal,
    currency: str,
) -> Money:
    """The discount one monetary promotion is worth on this cart."""
    if promotion.benefit_type == BenefitType.BXGY:
        return bxgy_amount(promotion, cart_items, currency)
    return benefit_amount(promotion, cart_items, items_total, currency)


def benefit_amount(
    promotion: Promotion,
    cart_items,
    items_total: Decimal,
    currency: str,
) -> Money:
    base = sum(
        (
            item.total_price.amount
            for item in matching_items(promotion, cart_items)
        ),
        Decimal(0),
    )
    if base <= 0:
        return Money(Decimal(0), currency)

    if promotion.benefit_type == BenefitType.PERCENTAGE:
        amount = base * promotion.benefit_value / Decimal(100)
    else:  # FIXED_AMOUNT
        amount = min(promotion.benefit_value, base)

    if promotion.max_discount_amount:
        amount = min(amount, Decimal(str(promotion.max_discount_amount.amount)))
    return Money(quantize(amount), currency)


def gift_entitlement(promotion: Promotion) -> GiftEntitlement | None:
    """FREE_GIFT reward: the (single) configured gift product.

    Chosen in memory from the prefetched rows. ``collect_candidates``
    prefetches ``get_products``, and a FILTERED related-manager query
    does not use that cache — ``.filter(active=True)`` builds a new
    queryset and always hits the database, so every eligible
    FREE_GIFT promotion cost one more query, which is the exact
    defect this change set removes elsewhere in the same file.
    ``min`` over the cached rows reproduces
    ``.filter(active=True).order_by("pk").first()`` exactly: lowest
    pk among the active ones, or None.

    A gift without the stock to hand over is no entitlement: order
    creation would skip it (``_inject_promotion_gifts``), so the
    cart, the product page and the coupon picker must not promise
    it either.
    """
    quantity = promotion.get_quantity or 1
    active = [p for p in promotion.get_products.all() if p.active]
    if not active:
        logger.warning(
            "FREE_GIFT promotion %s has no active gift product "
            "configured — skipping",
            promotion.pk,
        )
        return None
    # Sold out is a normal state, not a misconfiguration: the offer
    # simply pauses until the gift is restocked, so nothing is logged
    # (this runs on every cart read).
    gift_product = min(
        (p for p in active if p.stock >= quantity),
        key=lambda p: p.pk,
        default=None,
    )
    if gift_product is None:
        return None
    return GiftEntitlement(promotion, gift_product, quantity)


def gift_pairs(result: CartDiscountResult) -> list[tuple[Product, int]]:
    """``(product, quantity)`` of the entitled gifts, as cart lines.

    Gifts become physical order lines (``_inject_promotion_gifts``),
    so everything that sizes the parcel counts them with the cart:
    the weight bracket ACS quotes, the weight cap, and the carrier's
    parcel size. Add these to the cart's own pairs on every surface
    that prices or sizes shipping (the quote, order creation, the
    shipment row, the options). The engine already
    leaves out a gift that is out of stock; one that loses a later
    stock race only makes the parcel look bigger, which is safe.
    """
    return [(gift.product, gift.quantity) for gift in result.gift_items]
