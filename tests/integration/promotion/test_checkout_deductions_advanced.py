"""Zero-total settlement, gift-line injection, and PI/loyalty parity.

The order-first path settles fully-covered orders without a provider;
FREE_GIFT entitlements land as zero-price lines with real stock
decrements; and the Stripe verification guard now prices loyalty the
same way redeem_points does.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import Mock, patch

import pytest
from djmoney.money import Money

from cart.factories import CartFactory, CartItemFactory
from country.factories import CountryFactory
from order.enum.status import PaymentStatus
from order.services import OrderService
from pay_way.factories import PayWayFactory
from product.factories import ProductFactory
from promotion.enum import BenefitType, PromotionTrigger
from promotion.factories import PromotionFactory
from user.factories import UserAccountFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def enable_promotions():
    def _get(key, default=None):
        return {
            "PROMOTIONS_ENABLED": True,
            "LOYALTY_ENABLED": True,
            "LOYALTY_REDEMPTION_RATIO_EUR": 100.0,
        }.get(key, default)

    with (
        patch("promotion.services.Setting.get", side_effect=_get),
        patch("loyalty.services.Setting.get", side_effect=_get),
    ):
        yield


@pytest.fixture
def checkout():
    user = UserAccountFactory()
    country = CountryFactory()
    cart = CartFactory(user=user)
    cart.items.all().delete()
    product = ProductFactory(
        stock=10,
        price=Money(Decimal("100"), "EUR"),
        discount_percent=Decimal("0"),
        vat=None,
        active=True,
    )
    CartItemFactory(cart=cart, product=product, quantity=1)
    pay_way = PayWayFactory(
        is_online_payment=False,
        cost=Money(Decimal("0"), "EUR"),
        free_threshold=Money(Decimal("0"), "EUR"),
    )
    shipping_address = {
        "first_name": "Eleni",
        "last_name": "Georgiou",
        "email": "eleni@example.com",
        "street": "Akadimias",
        "street_number": "3",
        "city": "Athens",
        "zipcode": "10671",
        "country_id": country.alpha_2,
        "phone": "+306900000002",
    }
    return {
        "user": user,
        "cart": cart,
        "pay_way": pay_way,
        "shipping_address": shipping_address,
        "product": product,
    }


class TestZeroTotalSettlement:
    def test_full_promotion_coverage_settles_as_discount(
        self, enable_promotions, checkout
    ):
        # 100% off everything + free shipping = zero total.
        PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_value=Decimal("100"),
        )
        PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_type=BenefitType.FREE_SHIPPING,
        )

        order = OrderService.create_order_from_cart_offline(
            cart=checkout["cart"],
            shipping_address=checkout["shipping_address"],
            pay_way=checkout["pay_way"],
            user=checkout["user"],
        )

        order.refresh_from_db()
        assert order.paid_amount.amount == Decimal("0")
        assert order.payment_status == PaymentStatus.COMPLETED
        assert order.payment_id == f"DISCOUNT_{order.uuid}"
        assert order.payment_method == "discount"


class TestGiftInjection:
    def _gift_promotion(self, gift_product):
        promotion = PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_type=BenefitType.FREE_GIFT,
            get_quantity=1,
        )
        promotion.get_products.add(gift_product)
        return promotion

    def test_gift_line_created_with_stock_decrement(
        self, enable_promotions, checkout
    ):
        gift_product = ProductFactory(
            price=Money(Decimal("15"), "EUR"),
            discount_percent=Decimal("0"),
            vat=None,
            stock=5,
            active=True,
        )
        self._gift_promotion(gift_product)

        order = OrderService.create_order_from_cart_offline(
            cart=checkout["cart"],
            shipping_address=checkout["shipping_address"],
            pay_way=checkout["pay_way"],
            user=checkout["user"],
        )

        gift_line = order.items.get(product=gift_product)
        assert gift_line.price.amount == Decimal("0")
        assert gift_line.quantity == 1
        gift_product.refresh_from_db()
        assert gift_product.stock == 4
        # The paid line is untouched and the total charges only it.
        assert order.total_price_items.amount == Decimal("100")
        assert order.metadata["promotion_gifts"][0]["product_id"] == (
            gift_product.id
        )

    def test_out_of_stock_gift_is_skipped_not_blocking(
        self, enable_promotions, checkout
    ):
        gift_product = ProductFactory(
            price=Money(Decimal("15"), "EUR"),
            discount_percent=Decimal("0"),
            vat=None,
            stock=0,
            active=True,
        )
        self._gift_promotion(gift_product)

        order = OrderService.create_order_from_cart_offline(
            cart=checkout["cart"],
            shipping_address=checkout["shipping_address"],
            pay_way=checkout["pay_way"],
            user=checkout["user"],
        )

        assert not order.items.filter(product=gift_product).exists()
        assert order.metadata.get("promotion_gifts_skipped") == [
            gift_product.id
        ]


class TestLoyaltyPaymentIntentParity:
    def test_verification_accepts_intent_priced_with_loyalty(
        self, enable_promotions, checkout
    ):
        from loyalty.models.transaction import PointsTransaction
        from order.stock import StockManager

        user = checkout["user"]
        PointsTransaction.objects.create(
            user=user, points=500, transaction_type="EARN"
        )
        StockManager.reserve_stock(
            product_id=checkout["product"].id,
            quantity=1,
            session_id=str(checkout["cart"].uuid),
            user_id=user.id,
        )

        pay_way = PayWayFactory(
            provider_code="stripe",
            is_online_payment=True,
            cost=Money(Decimal("0"), "EUR"),
            free_threshold=Money(Decimal("0"), "EUR"),
        )

        # Expected charge: 100 items − 5.00 loyalty (500 pts @ 100/EUR)
        # + generic shipping (free ≥ threshold at 100 EUR carts).
        expected_total = OrderService.calculate_shipping_cost(
            order_value=Money(Decimal("100"), "EUR"),
            country_id=checkout["shipping_address"]["country_id"],
            region_id=None,
            shipping_provider_code=None,
            shipping_kind=None,
            weight_grams=0,
        ).amount + Decimal("95.00")

        with patch("order.payment.get_payment_provider") as mock_provider:
            instance = Mock()
            instance.get_payment_status.return_value = (
                PaymentStatus.COMPLETED,
                {
                    "payment_id": "pi_loyalty",
                    "amount": float(expected_total),
                    "currency": "eur",
                },
            )
            mock_provider.return_value = instance

            order = OrderService.create_order_from_cart(
                cart=checkout["cart"],
                shipping_address=checkout["shipping_address"],
                payment_intent_id="pi_loyalty",
                pay_way=pay_way,
                user=user,
                loyalty_points_to_redeem=500,
            )

        order.refresh_from_db()
        assert order.loyalty_discount.amount == Decimal("5.00")
        assert order.paid_amount.amount == expected_total

    def test_verification_rejects_stale_undiscounted_intent(
        self, enable_promotions, checkout
    ):
        from loyalty.models.transaction import PointsTransaction
        from order.exceptions import PaymentAmountMismatchError
        from order.stock import StockManager

        user = checkout["user"]
        PointsTransaction.objects.create(
            user=user, points=500, transaction_type="EARN"
        )
        StockManager.reserve_stock(
            product_id=checkout["product"].id,
            quantity=1,
            session_id=str(checkout["cart"].uuid),
            user_id=user.id,
        )
        pay_way = PayWayFactory(
            provider_code="stripe",
            is_online_payment=True,
            cost=Money(Decimal("0"), "EUR"),
            free_threshold=Money(Decimal("0"), "EUR"),
        )
        undiscounted = OrderService.calculate_shipping_cost(
            order_value=Money(Decimal("100"), "EUR"),
            country_id=checkout["shipping_address"]["country_id"],
            region_id=None,
            shipping_provider_code=None,
            shipping_kind=None,
            weight_grams=0,
        ).amount + Decimal("100.00")

        with patch("order.payment.get_payment_provider") as mock_provider:
            instance = Mock()
            instance.get_payment_status.return_value = (
                PaymentStatus.COMPLETED,
                {
                    "payment_id": "pi_stale",
                    "amount": float(undiscounted),
                    "currency": "eur",
                },
            )
            mock_provider.return_value = instance

            with pytest.raises(PaymentAmountMismatchError):
                OrderService.create_order_from_cart(
                    cart=checkout["cart"],
                    shipping_address=checkout["shipping_address"],
                    payment_intent_id="pi_stale",
                    pay_way=pay_way,
                    user=user,
                    loyalty_points_to_redeem=500,
                )
