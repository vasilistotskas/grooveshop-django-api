"""The redemption rows and order metadata an evaluated result leaves."""

from __future__ import annotations

from django.conf import settings
from djmoney.money import Money

from promotion.engine.results import CartDiscountResult
from promotion.models import PromotionRedemption


def record_redemptions(order, result: CartDiscountResult) -> Money:
    """Persist redemption rows for an evaluated result.

    Must run inside the same transaction (and Promotion locks) as
    the ``evaluate(lock=True)`` call that produced ``result``.
    Returns the discount total to store on the order.
    """
    for entry in result.applied:
        PromotionRedemption.objects.create(
            promotion=entry.promotion,
            code=entry.code,
            order=order,
            user=order.user,
            email=order.email or "",
            amount=entry.amount,
        )
    # Zero-amount rows for FREE_SHIPPING / FREE_GIFT applications —
    # without them their usage limits would never count anything.
    for promotion, code in result.non_monetary:
        PromotionRedemption.objects.create(
            promotion=promotion,
            code=code,
            order=order,
            user=order.user,
            email=order.email or "",
            amount=Money(0, settings.DEFAULT_CURRENCY),
        )
    if result.applied:
        order.metadata["promotions"] = [
            {
                "promotion_id": entry.promotion.id,
                "name": entry.promotion.safe_translation_getter(
                    "name", any_language=True
                )
                or "",
                "code": entry.code.code if entry.code else None,
                "amount": str(entry.amount.amount),
            }
            for entry in result.applied
        ]
    if result.gift_items:
        order.metadata["promotion_gifts"] = [
            {
                "promotion_id": gift.promotion.id,
                "product_id": gift.product.id,
                "quantity": gift.quantity,
            }
            for gift in result.gift_items
        ]
    if result.free_shipping:
        order.metadata["promotion_free_shipping"] = True
    return result.discount_total
