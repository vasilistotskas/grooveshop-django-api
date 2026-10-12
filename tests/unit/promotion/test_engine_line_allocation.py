"""Where a promotion's money lands: the per-line allocation.

``PromotionEngine`` reports, for every applied monetary promotion, the part
of its amount each cart line it covers carries (``AppliedPromotion.
line_amounts``), to the cent, summing exactly to the promotion's amount.
The cart preview, the payment-intent amount and order creation all read the
same evaluation, so they cannot disagree about it.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from promotion.enum import BenefitType, PromotionTrigger, TargetScope
from promotion.factories import PromotionFactory
from promotion.services import PromotionEngine

pytestmark = pytest.mark.django_db

D = Decimal


def _lines(cart):
    """Cart item pk by the line's order in ``make_cart``."""
    return [item.pk for item in cart.items.order_by("pk")]


def _promotion(**kwargs):
    kwargs.setdefault("stackable", True)
    return PromotionFactory(trigger=PromotionTrigger.AUTOMATIC, **kwargs)


class TestLineAmounts:
    def test_an_order_wide_promotion_spreads_over_the_lines_by_value(
        self, enable_promotions, make_cart
    ):
        cart, _ = make_cart([(40, 1), (10, 2)])
        _promotion(benefit_value=D(10))

        result = PromotionEngine.evaluate(cart)

        first, second = _lines(cart)
        assert result.line_discounts == {first: D("4.00"), second: D("2.00")}

    def test_the_lines_sum_exactly_to_the_amount_to_the_cent(
        self, enable_promotions, make_cart
    ):
        cart, _ = make_cart([("19.99", 1), ("7.77", 2), ("3.33", 3)])
        _promotion(benefit_value=D("33.3"))

        result = PromotionEngine.evaluate(cart)

        (entry,) = result.applied
        assert sum(entry.line_amounts.values()) == entry.amount.amount
        assert (
            sum(result.line_discounts.values()) == result.discount_total.amount
        )
        assert all(
            amount == amount.quantize(D("0.01"))
            for amount in entry.line_amounts.values()
        )

    def test_a_product_scope_never_reaches_the_other_lines(
        self, enable_promotions, make_cart
    ):
        cart, products = make_cart([(100, 1), (50, 2)])
        promotion = _promotion(
            target_scope=TargetScope.PRODUCTS, benefit_value=D(10)
        )
        promotion.products.add(products[0])

        result = PromotionEngine.evaluate(cart)

        assert result.line_discounts == {_lines(cart)[0]: D("10.00")}

    def test_an_excluded_product_carries_nothing_of_an_order_wide_one(
        self, enable_promotions, make_cart
    ):
        cart, products = make_cart([(60, 1), (40, 1)])
        promotion = _promotion(benefit_value=D(10))
        promotion.excluded_products.add(products[1])

        result = PromotionEngine.evaluate(cart)

        assert result.line_discounts == {_lines(cart)[0]: D("6.00")}

    def test_buy_x_get_y_lands_on_the_lines_of_the_discounted_units(
        self, enable_promotions, make_cart
    ):
        cart, _ = make_cart([(5, 1), (20, 2)])
        _promotion(
            benefit_type=BenefitType.BXGY,
            benefit_value=D(0),
            buy_quantity=2,
            get_quantity=1,
            get_discount_percent=D(100),
        )

        result = PromotionEngine.evaluate(cart)

        assert result.line_discounts == {_lines(cart)[0]: D("5.00")}

    def test_two_stacked_promotions_never_take_a_line_below_zero(
        self, enable_promotions, make_cart
    ):
        cart, products = make_cart([(100, 1), (100, 1)])
        scoped = _promotion(
            target_scope=TargetScope.PRODUCTS,
            benefit_value=D(100),
            priority=1,
        )
        scoped.products.add(products[0])
        _promotion(
            benefit_type=BenefitType.FIXED_AMOUNT,
            benefit_value=D(30),
            priority=2,
        )

        result = PromotionEngine.evaluate(cart)

        first, second = _lines(cart)
        # The first line is empty after the scoped promotion, so the
        # order-wide fixed amount lands on the second alone.
        assert result.line_discounts == {first: D("100.00"), second: D("30.00")}
        assert result.discount_total.amount == D("130.00")

    def test_a_promotion_worth_more_than_its_lines_hold_is_cut_to_them(
        self, enable_promotions, make_cart
    ):
        cart, products = make_cart([(30, 1), (500, 1)])
        promotion = _promotion(
            target_scope=TargetScope.PRODUCTS,
            benefit_type=BenefitType.FIXED_AMOUNT,
            benefit_value=D(100),
        )
        promotion.products.add(products[0])

        result = PromotionEngine.evaluate(cart)

        assert result.discount_total.amount == D("30.00")
        assert result.line_discounts == {_lines(cart)[0]: D("30.00")}
