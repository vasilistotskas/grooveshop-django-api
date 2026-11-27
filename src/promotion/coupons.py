"""Attach and detach a cart's coupon code."""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction

from promotion.engine.eligibility import dead_window_reason
from promotion.engine.evaluation import PromotionEngine
from promotion.engine.results import CartDiscountResult
from promotion.enum import CouponRejectionReason, PromotionTrigger
from promotion.models import CartPromotionCode, PromotionCode


class CouponError(ValidationError):
    """A coupon code was refused; ``reason`` is machine-readable."""

    def __init__(self, reason: str):
        self.reason = str(reason)
        label = dict(CouponRejectionReason.choices).get(
            reason, CouponRejectionReason.INVALID.label
        )
        super().__init__(str(label))


class CouponService:
    """Attach/detach coupon codes on a cart."""

    @classmethod
    def apply(cls, cart, raw_code: str, *, user=None) -> CartDiscountResult:
        if not PromotionEngine.is_enabled():
            raise CouponError(CouponRejectionReason.INVALID)

        normalized = (raw_code or "").strip().upper()
        try:
            code = PromotionCode.objects.select_related("promotion").get(
                code=normalized,
                is_active=True,
                promotion__trigger=PromotionTrigger.CODE,
            )
        except PromotionCode.DoesNotExist as exc:
            raise CouponError(CouponRejectionReason.INVALID) from exc

        if not code.promotion.is_live:
            raise CouponError(dead_window_reason(code.promotion))

        with transaction.atomic():
            # Lock the cart row before touching CartPromotionCode so applying
            # a coupon takes locks in the same Cart -> CartPromotionCode order
            # as checkout and payment settlement's cart cleanup (#839).
            from cart.models import Cart

            if (
                Cart.objects.select_for_update().filter(pk=cart.pk).first()
                is None
            ):
                raise CouponError(CouponRejectionReason.INVALID)
            # v1 policy: one coupon per cart — applying a new code
            # replaces the previous one (schema supports several).
            CartPromotionCode.objects.filter(cart=cart).delete()
            CartPromotionCode.objects.create(cart=cart, code=code)

        result = PromotionEngine.evaluate(cart, user=user)
        rejected = {c: r for c, r in result.rejected}
        if normalized in rejected:
            # The code exists but this cart/customer cannot use it —
            # detach it again and surface the precise reason.
            CartPromotionCode.objects.filter(cart=cart).delete()
            raise CouponError(rejected[normalized])
        return result

    @classmethod
    def remove(cls, cart) -> None:
        CartPromotionCode.objects.filter(cart=cart).delete()

    @classmethod
    def clear_after_order(cls, cart) -> None:
        """Detach codes once the order is placed (the redemption rows
        are the durable record)."""
        CartPromotionCode.objects.filter(cart=cart).delete()
