"""End-to-end promotion behaviour through the order-creation service.

The COD (order-first) path is the one with NO payment-amount guard —
if the discount silently dropped there, the courier would collect the
undiscounted figure. These tests pin the full flow: cart attachment →
locked evaluation → order fields → redemption rows → typed error when
a coupon dies between preview and checkout.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest
from djmoney.money import Money

from cart.factories import CartFactory, CartItemFactory
from country.factories import CountryFactory
from order.exceptions import InvalidCouponError
from order.services import OrderService
from pay_way.factories import PayWayFactory
from product.factories import ProductFactory
from promotion.enum import BenefitType, PromotionTrigger
from promotion.factories import PromotionCodeFactory, PromotionFactory
from promotion.models import CartPromotionCode, PromotionRedemption
from user.factories import UserAccountFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def enable_promotions():
    def _get(key, default=None):
        return {"PROMOTIONS_ENABLED": True}.get(key, default)

    with patch("promotion.services.Setting.get", side_effect=_get):
        yield


@pytest.fixture
def checkout():
    """Cart with one 100 EUR line + everything the offline path needs."""
    user = UserAccountFactory()
    country = CountryFactory()
    cart = CartFactory(user=user)
    cart.items.all().delete()
    product = ProductFactory(
        stock=10,
        price=Money(Decimal(100), "EUR"),
        discount_percent=Decimal(0),
        vat=None,
        active=True,
    )
    CartItemFactory(cart=cart, product=product, quantity=1)
    pay_way = PayWayFactory(
        is_online_payment=False,
        cost=Money(Decimal(0), "EUR"),
        free_threshold=Money(Decimal(0), "EUR"),
    )
    shipping_address = {
        "first_name": "Maria",
        "last_name": "Papadopoulou",
        "email": "maria@example.com",
        "street": "Ermou",
        "street_number": "1",
        "city": "Athens",
        "zipcode": "10563",
        "country_id": country.alpha_2,
        "phone": "+306900000000",
    }
    return {
        "user": user,
        "cart": cart,
        "pay_way": pay_way,
        "shipping_address": shipping_address,
        "product": product,
    }


def _create_offline_order(checkout):
    return OrderService.create_order_from_cart_offline(
        cart=checkout["cart"],
        shipping_address=checkout["shipping_address"],
        pay_way=checkout["pay_way"],
        user=checkout["user"],
    )


class TestOfflineCheckoutWithCoupon:
    def test_coupon_discount_reaches_paid_amount(
        self, enable_promotions, checkout
    ):
        code = PromotionCodeFactory(
            promotion=PromotionFactory(benefit_value=Decimal(10))
        )
        CartPromotionCode.objects.create(cart=checkout["cart"], code=code)

        order = _create_offline_order(checkout)

        order.refresh_from_db()
        assert order.discount_amount.amount == Decimal("10.00")
        assert order.paid_amount.amount == order.total_price.amount - Decimal(
            "10.00"
        )
        redemption = PromotionRedemption.objects.get(order=order)
        assert redemption.code == code
        assert redemption.user == checkout["user"]
        # The cart attachment is consumed once the order exists.
        assert not CartPromotionCode.objects.filter(
            cart=checkout["cart"]
        ).exists()
        assert order.metadata["promotions"][0]["code"] == code.code

    def test_exhausted_coupon_aborts_with_typed_error(
        self, enable_promotions, checkout
    ):
        promotion = PromotionFactory(usage_limit_total=1)
        code = PromotionCodeFactory(promotion=promotion)
        PromotionRedemption.objects.create(
            promotion=promotion,
            code=code,
            amount=Money(Decimal(5), "EUR"),
        )
        CartPromotionCode.objects.create(cart=checkout["cart"], code=code)

        with pytest.raises(InvalidCouponError) as excinfo:
            _create_offline_order(checkout)

        assert excinfo.value.code == code.code
        assert "usage_limit" in excinfo.value.reason

    def test_free_shipping_promotion_zeroes_shipping_price(
        self, enable_promotions, checkout
    ):
        PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_type=BenefitType.FREE_SHIPPING,
        )

        order = _create_offline_order(checkout)

        assert order.shipping_price.amount == Decimal(0)
        assert order.metadata.get("promotion_free_shipping") is True

    def test_automatic_promotion_applies_without_code(
        self, enable_promotions, checkout
    ):
        PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_type=BenefitType.FIXED_AMOUNT,
            benefit_value=Decimal(15),
        )

        order = _create_offline_order(checkout)

        assert order.discount_amount.amount == Decimal("15.00")
        assert PromotionRedemption.objects.filter(order=order).exists()

    def test_disabled_feature_means_no_discount(self, checkout):
        PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_value=Decimal(50),
        )

        order = _create_offline_order(checkout)

        assert order.discount_amount.amount == Decimal(0)

    def test_usage_limits_hold_for_consecutive_orders(
        self, enable_promotions, checkout
    ):
        promotion = PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            usage_limit_total=1,
            benefit_value=Decimal(10),
        )

        first = _create_offline_order(checkout)
        assert first.discount_amount.amount == Decimal("10.00")

        # Second shopper: the automatic promotion is exhausted — the
        # order still succeeds, just without the discount.
        second_user = UserAccountFactory()
        cart = CartFactory(user=second_user)
        cart.items.all().delete()
        CartItemFactory(cart=cart, product=checkout["product"], quantity=1)
        second = OrderService.create_order_from_cart_offline(
            cart=cart,
            shipping_address={
                **checkout["shipping_address"],
                "email": "second@example.com",
            },
            pay_way=checkout["pay_way"],
            user=second_user,
        )

        assert second.discount_amount.amount == Decimal(0)
        assert (
            PromotionRedemption.objects.filter(promotion=promotion).count() == 1
        )
