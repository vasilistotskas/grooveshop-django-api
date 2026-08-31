"""Promotions must not stack on wholesale prices unless the merchant
opts in (B2B_ALLOW_PROMOTIONS). One gate inside PromotionEngine.evaluate
covers every caller — cart preview, payment intent, order create — so a
coupon can never show no discount in preview yet charge one at create
(or vice versa).
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest

from b2b.factories import CustomerGroupFactory
from b2b.services import B2BPricingService
from promotion.enum import PromotionTrigger
from promotion.factories import PromotionFactory
from promotion.services import PromotionEngine

pytestmark = pytest.mark.django_db


def _bind(cart):
    from b2b.services import B2BPricingContext

    group = CustomerGroupFactory(discount_percent=Decimal("10"))
    cart._b2b_pricing = B2BPricingContext(
        group=group,
        prices=B2BPricingService.resolve_map(
            [item.product for item in cart.items.all()], group
        ),
    )


class TestB2BPromotionGate:
    def test_bound_cart_gets_no_promotions_by_default(
        self, enable_promotions, make_cart
    ):
        cart, _ = make_cart([(100, 1)])
        PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_value=Decimal("10"),
        )
        _bind(cart)

        result = PromotionEngine.evaluate(cart)

        assert result.applied == []
        assert result.discount_total.amount == 0

    def test_merchant_opt_in_restores_stacking(
        self, enable_promotions, make_cart
    ):
        cart, _ = make_cart([(100, 1)])
        PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_value=Decimal("10"),
        )
        _bind(cart)

        def _get(key, default=None):
            # ``b2b.services.Setting`` and ``promotion.services.Setting``
            # are the SAME class — this inner patch replaces the
            # enable_promotions fixture's stub, so it must keep
            # PROMOTIONS_ENABLED answering True as well.
            return {
                "B2B_ALLOW_PROMOTIONS": True,
                "PROMOTIONS_ENABLED": True,
            }.get(key, default)

        with patch("b2b.services.Setting.get", side_effect=_get):
            result = PromotionEngine.evaluate(cart)

        # 10% now applies ON TOP of the bound line prices (100 → 90 net,
        # no VAT in this fixture → items total 90).
        assert result.discount_total.amount == Decimal("9.00")

    def test_retail_cart_unaffected(self, enable_promotions, make_cart):
        cart, _ = make_cart([(100, 1)])
        PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_value=Decimal("10"),
        )

        result = PromotionEngine.evaluate(cart)

        assert result.discount_total.amount == Decimal("10.00")

    def test_gated_cart_marks_attached_codes_combination_disallowed(
        self, enable_promotions, make_cart
    ):
        """An attached coupon on a gated wholesale cart surfaces as
        COMBINATION_DISALLOWED (non-blocking): apply_coupon refuses new
        codes with that reason, and order create proceeds without a
        discount — exactly what the preview showed."""
        from promotion.enum import CouponRejectionReason
        from promotion.factories import (
            PromotionCodeFactory,
            PromotionFactory as PF,
        )
        from promotion.models import CartPromotionCode

        cart, _ = make_cart([(100, 1)])
        code = PromotionCodeFactory(promotion=PF(benefit_value=Decimal("10")))
        CartPromotionCode.objects.create(cart=cart, code=code)
        _bind(cart)

        result = PromotionEngine.evaluate(cart)

        assert (
            code.code,
            str(CouponRejectionReason.COMBINATION_DISALLOWED),
        ) in result.rejected
        # Non-blocking: COMBINATION_DISALLOWED never aborts order create.
        assert result.blocking_rejections == []

    def test_bxgy_credits_wholesale_unit_prices(
        self, enable_promotions, make_cart
    ):
        """Buy-2-get-1 on a bound cart discounts the WHOLESALE unit,
        not the retail one (regression: _unit_prices read
        product.final_price)."""
        from promotion.enum import BenefitType

        cart, _products = make_cart([(100, 3)])
        PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_type=BenefitType.BXGY,
            buy_quantity=2,
            get_quantity=1,
            get_discount_percent=Decimal("100"),
        )
        _bind(cart)  # 10% group → unit 90.00

        def _get(key, default=None):
            return {
                "B2B_ALLOW_PROMOTIONS": True,
                "PROMOTIONS_ENABLED": True,
            }.get(key, default)

        with patch("b2b.services.Setting.get", side_effect=_get):
            result = PromotionEngine.evaluate(cart)

        # One free unit at the WHOLESALE price.
        assert result.discount_total.amount == Decimal("90.00")
