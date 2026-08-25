"""Gift-card redemption through the order-creation service.

Split payment (card + COD remainder), full coverage (no provider at
all), the insufficient-coverage guard for intent-less online checkouts,
and the refund credit round-trip.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest
from djmoney.money import Money

from cart.factories import CartFactory, CartItemFactory
from country.factories import CountryFactory
from giftcard.enum import GiftCardTransactionKind
from giftcard.factories import GiftCardFactory
from giftcard.services import GiftCardService
from order.exceptions import InvalidGiftCardError
from order.services import OrderService
from pay_way.factories import PayWayFactory
from product.factories import ProductFactory
from user.factories import UserAccountFactory

pytestmark = pytest.mark.django_db

GIFT_CARD_SETTINGS = {
    "GIFT_CARDS_ENABLED": True,
    "GIFT_CARD_VALIDITY_DAYS": 1825,
}


@pytest.fixture
def enable_gift_cards():
    def _get(key, default=None):
        return GIFT_CARD_SETTINGS.get(key, default)

    with patch("giftcard.services.Setting.get", side_effect=_get):
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
        "first_name": "Nikos",
        "last_name": "Ioannou",
        "email": "nikos@example.com",
        "street": "Stadiou",
        "street_number": "5",
        "city": "Athens",
        "zipcode": "10564",
        "country_id": country.alpha_2,
        "phone": "+306900000001",
    }
    return {
        "user": user,
        "cart": cart,
        "pay_way": pay_way,
        "shipping_address": shipping_address,
    }


def _create_order(checkout, codes):
    return OrderService.create_order_from_cart_offline(
        cart=checkout["cart"],
        shipping_address=checkout["shipping_address"],
        pay_way=checkout["pay_way"],
        user=checkout["user"],
        gift_card_codes=codes,
    )


class TestSplitPayment:
    def test_partial_coverage_leaves_remainder_for_cod(
        self, enable_gift_cards, checkout
    ):
        card = GiftCardFactory(initial_value=Money(Decimal("30"), "EUR"))

        order = _create_order(checkout, [card.code])

        order.refresh_from_db()
        assert order.gift_card_amount.amount == Decimal("30.00")
        assert order.paid_amount.amount == order.total_price.amount - Decimal(
            "30.00"
        )
        assert order.payment_status != "COMPLETED"
        assert card.balance.amount == Decimal("0")
        redeem = card.transactions.get(kind=GiftCardTransactionKind.REDEEM)
        assert redeem.order == order
        assert order.metadata["gift_cards"][0]["code"] == card.code

    def test_full_coverage_settles_without_provider(
        self, enable_gift_cards, checkout
    ):
        card = GiftCardFactory(initial_value=Money(Decimal("500"), "EUR"))

        order = _create_order(checkout, [card.code])

        order.refresh_from_db()
        total = order.total_price.amount
        assert order.gift_card_amount.amount == total
        assert order.paid_amount.amount == Decimal("0")
        assert order.payment_status == "COMPLETED"
        assert order.payment_id == f"GIFTCARD_{order.uuid}"
        assert order.payment_method == "gift_card"
        # The card keeps whatever the order didn't need.
        assert card.balance.amount == Decimal("500") - total

    def test_insufficient_cards_block_intent_less_online_checkout(
        self, enable_gift_cards, checkout
    ):
        online = PayWayFactory(
            provider_code="stripe",
            is_online_payment=True,
            cost=Money(Decimal("0"), "EUR"),
            free_threshold=Money(Decimal("0"), "EUR"),
        )
        card = GiftCardFactory(initial_value=Money(Decimal("10"), "EUR"))

        with pytest.raises(InvalidGiftCardError) as excinfo:
            OrderService.create_order_from_cart_offline(
                cart=checkout["cart"],
                shipping_address=checkout["shipping_address"],
                pay_way=online,
                user=checkout["user"],
                gift_card_codes=[card.code],
            )

        assert excinfo.value.reason == "gift_card_insufficient"

    def test_refund_credits_the_source_card(self, enable_gift_cards, checkout):
        card = GiftCardFactory(initial_value=Money(Decimal("30"), "EUR"))
        order = _create_order(checkout, [card.code])
        assert card.balance.amount == Decimal("0")

        GiftCardService.credit_refund(order)

        assert card.balance.amount == Decimal("30")
