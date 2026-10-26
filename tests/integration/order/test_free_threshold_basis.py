"""Free shipping and a free payment fee are judged on ONE value.

``ShippingRate.free_shipping_threshold`` and ``PayWay.free_threshold`` both
mean "free above X" to the shopper, and X is the products after promotion
and coupon discounts, excluding shipping and fees (``OrderService.
threshold_value``). Before, the fee compared items + shipping, so a EUR 39.98
cart with a EUR 40 threshold paid for shipping but not for the COD fee, and
free shipping compared the gross cart total.

Each scenario is checked three ways, which must agree: the cart's payment
intent preview, offline order creation, and payment-first order creation
(which refuses an intent whose amount is not the total it computes).
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest
from django.urls import reverse
from djmoney.money import Money
from rest_framework.test import APIClient

from cart.factories import CartFactory, CartItemFactory
from country.factories import CountryFactory
from order.services import OrderService
from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory
from product.factories import ProductFactory
from promotion.enum import BenefitType, PromotionTrigger
from promotion.factories import PromotionFactory
from tests.utils.fake_stripe import fake_stripe
from tests.utils.shipping import enable_rate
from user.factories import UserAccountFactory

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.assert_english,
    pytest.mark.usefixtures("checkout_legal_documents"),
]

THRESHOLD = Decimal("40.00")
SHIPPING = Decimal("3.00")
FEE = Decimal("2.99")


@pytest.fixture(autouse=True)
def _promotions_on():
    def _get(key, default=None):
        return {"PROMOTIONS_ENABLED": True}.get(key, default)

    with patch("extra_settings.models.Setting.get", side_effect=_get):
        yield


def _checkout(products: str, discount: str | None):
    user = UserAccountFactory(num_addresses=0)
    country = CountryFactory()
    enable_rate(country, price=SHIPPING, free_shipping_threshold=THRESHOLD)
    cart = CartFactory(user=user)
    cart.items.all().delete()
    CartItemFactory(
        cart=cart,
        product=ProductFactory(
            stock=10,
            price=Money(Decimal(products), "EUR"),
            discount_percent=Decimal(0),
            vat=None,
            active=True,
            num_images=0,
            num_reviews=0,
        ),
        quantity=1,
    )
    if discount:
        PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_type=BenefitType.FIXED_AMOUNT,
            benefit_value=Decimal(discount),
        )
    pay_way = PayWayFactory(
        provider_code="stripe",
        settlement=PaySettlement.ONLINE,
        active=True,
        cost=Money(FEE, "EUR"),
        free_threshold=Money(THRESHOLD, "EUR"),
    )
    address = {
        "first_name": "Eleni",
        "last_name": "Georgiou",
        "email": "eleni@example.com",
        "street": "Akadimias",
        "street_number": "3",
        "city": "Athens",
        "zipcode": "10671",
        "country_id": country.alpha_2,
        "phone": "+306900000002",
        "shipping_kind": "home_delivery",
    }
    return user, cart, pay_way, address


def _preview(user, cart, pay_way, address) -> tuple[Decimal, str]:
    client = APIClient()
    client.force_authenticate(user=user)
    response = client.post(
        reverse("cart-create-payment-intent"),
        {
            "pay_way_id": pay_way.id,
            "shipping_kind": "home_delivery",
            "country_id": address["country_id"],
        },
        format="json",
        HTTP_X_CART_ID=str(cart.uuid),
    )
    assert response.status_code == 200, response.content
    return Decimal(str(response.data["amount"])), response.data[
        "payment_intent_id"
    ]


SCENARIOS = [
    pytest.param(
        "39.98", None, False, id="39.98 of products: shipping and fee charged"
    ),
    pytest.param("40.00", None, True, id="40.00 of products: both free"),
    pytest.param(
        "45.00", "6.00", False, id="45.00 less a 6.00 discount: neither free"
    ),
    pytest.param(
        "46.00", "6.00", True, id="46.00 less a 6.00 discount: both free"
    ),
]


@pytest.mark.parametrize(("products", "discount", "free"), SCENARIOS)
def test_the_preview_and_both_creation_paths_agree(products, discount, free):
    user, cart, pay_way, address = _checkout(products, discount)
    shipping = Decimal(0) if free else SHIPPING
    fee = Decimal(0) if free else FEE
    items = Decimal(products) - Decimal(discount or 0)

    with fake_stripe():
        amount, intent_id = _preview(user, cart, pay_way, address)
        assert amount == items + shipping + fee

        # Payment-first creation refuses an intent whose amount is not the
        # total it computes, so accepting this one proves it agrees.
        order = OrderService.create_order_from_cart(
            cart=cart,
            shipping_address=address,
            payment_intent_id=intent_id,
            pay_way=pay_way,
            user=user,
        )
    assert order.shipping_price.amount == shipping
    assert order.payment_method_fee.amount == fee


@pytest.mark.parametrize(("products", "discount", "free"), SCENARIOS)
def test_offline_creation_charges_the_same(products, discount, free):
    """The owner's case: cash on delivery, EUR 2.99, free at EUR 40."""
    user, cart, _stripe, address = _checkout(products, discount)
    pay_way = PayWayFactory(
        settlement=PaySettlement.COURIER_CASH,
        active=True,
        cost=Money(FEE, "EUR"),
        free_threshold=Money(THRESHOLD, "EUR"),
    )

    order = OrderService.create_order_from_cart_offline(
        cart=cart, shipping_address=address, pay_way=pay_way, user=user
    )

    assert order.shipping_price.amount == (Decimal(0) if free else SHIPPING)
    assert order.payment_method_fee.amount == (Decimal(0) if free else FEE)


class TestTheCartReportsTheValue:
    @pytest.mark.parametrize(
        ("products", "discount", "expected"),
        [("39.98", None, "39.98"), ("45.00", "6.00", "39.00")],
    )
    def test_free_threshold_value(self, products, discount, expected):
        user, _cart, _pay_way, _address = _checkout(products, discount)
        client = APIClient()
        client.force_authenticate(user=user)

        response = client.get(reverse("cart-detail"))

        assert response.status_code == 200, response.content
        assert Decimal(str(response.data["free_threshold_value"])) == Decimal(
            expected
        )
