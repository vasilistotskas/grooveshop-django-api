"""The suggestion-strip impression a cart line carries survives
checkout: the order line inherits it, so the recommendation engine can
attach the line to the impression that earned it without needing a
cart or customer identity from the time the strip was shown.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import patch

import pytest
from djmoney.money import Money

from cart.factories import CartFactory, CartItemFactory
from country.factories import CountryFactory
from order.enum.status import PaymentStatus
from order.services import OrderService
from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory
from product.factories import ProductFactory
from user.factories import UserAccountFactory

pytestmark = pytest.mark.django_db


def _product():
    return ProductFactory(
        stock=10,
        price=Money(Decimal("10.00"), "EUR"),
        discount_percent=Decimal(0),
        active=True,
    )


def _checkout(cart):
    country = CountryFactory()
    return OrderService.create_order_from_cart_offline(
        cart=cart,
        shipping_address={
            "first_name": "Maria",
            "last_name": "Papadopoulou",
            "email": "maria@example.com",
            "street": "Ermou",
            "street_number": "1",
            "city": "Athens",
            "zipcode": "10563",
            "country_id": country.alpha_2,
            "phone": "+306900000000",
        },
        pay_way=PayWayFactory(
            settlement=PaySettlement.COURIER_CASH,
            active=True,
            cost=Money(Decimal(0), "EUR"),
            free_threshold=Money(Decimal(0), "EUR"),
        ),
        user=None,
    )


def _lines(order):
    return {
        line.product_id: line.recommendation_impression_id
        for line in order.items.all()
    }


def test_the_offline_order_line_inherits_the_cart_line_impression():
    cart = CartFactory(is_guest=True)
    cart.items.all().delete()
    impression = uuid.uuid4()
    carried = _product()
    plain = _product()
    CartItemFactory(
        cart=cart,
        product=carried,
        quantity=2,
        recommendation_impression_id=impression,
    )
    CartItemFactory(cart=cart, product=plain, quantity=1)

    order = _checkout(cart)

    assert _lines(order) == {carried.id: impression, plain.id: None}


def test_the_payment_first_order_line_inherits_the_cart_line_impression():
    user = UserAccountFactory()
    cart = CartFactory(user=user)
    cart.items.all().delete()
    impression = uuid.uuid4()
    carried = _product()
    plain = _product()
    CartItemFactory(
        cart=cart,
        product=carried,
        quantity=1,
        recommendation_impression_id=impression,
    )
    CartItemFactory(cart=cart, product=plain, quantity=1)
    country = CountryFactory()

    with patch("order.payment.get_payment_provider") as provider:
        provider.return_value.get_payment_status.return_value = (
            PaymentStatus.COMPLETED,
            {"payment_id": "pi_test_123", "status": "succeeded"},
        )
        order = OrderService.create_order_from_cart(
            cart=cart,
            shipping_address={
                "first_name": "Maria",
                "last_name": "Papadopoulou",
                "email": "maria@example.com",
                "street": "Ermou",
                "street_number": "1",
                "city": "Athens",
                "zipcode": "10563",
                "country_id": country.alpha_2,
                "phone": "+306900000000",
            },
            payment_intent_id="pi_test_123",
            pay_way=PayWayFactory(provider_code="stripe"),
            user=user,
        )

    assert _lines(order) == {carried.id: impression, plain.id: None}
