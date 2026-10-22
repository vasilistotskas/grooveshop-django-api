"""Free gifts ride in the parcel, so they count for its size.

``_inject_promotion_gifts`` turns every entitled gift into a physical
order line, so a gift with dimensions can need a bigger BoxNow
compartment than the cart's own lines. Every surface that sizes the
parcel adds ``PromotionEngine.gift_pairs`` to the cart's pairs: the
options flag, the shipping check, and the shipment row's size.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest
from django.urls import reverse
from djmoney.money import Money
from measurement.measures import Distance
from rest_framework import status
from rest_framework.test import APIClient

from cart.factories import CartFactory, CartItemFactory
from country.factories import CountryFactory
from order.services import OrderService
from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory
from product.factories import ProductFactory
from promotion.enum import BenefitType, PromotionTrigger
from promotion.factories import PromotionFactory
from promotion.services import PromotionEngine
from shipping.models import ShippingProvider
from shipping_boxnow.enum import BoxNowCompartmentSize
from shipping_boxnow.factories import BoxNowLockerFactory
from shipping_boxnow.models import BoxNowShipment
from tests.utils.shipping import enable_rate
from user.factories import UserAccountFactory

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("trust_intent_binding", "boxnow_configured_tenant"),
]


@pytest.fixture(autouse=True)
def promotions_on():
    def _get(key, default=None):
        return {"PROMOTIONS_ENABLED": True}.get(key, default)

    # The BoxNow tenant stand-in carries no plan flags.
    with (
        patch("extra_settings.models.Setting.get", side_effect=_get),
        patch("tenant.membership.tenant_plan_allows", return_value=True),
    ):
        yield


def _product(*, cm, stock=10, **kwargs):
    product = ProductFactory(
        stock=stock,
        price=Money(Decimal(100), "EUR"),
        discount_percent=Decimal(0),
        vat=None,
        active=True,
        num_images=0,
        num_reviews=0,
        **kwargs,
    )
    length, width, height = cm
    product.length = Distance(cm=length)
    product.width = Distance(cm=width)
    product.height = Distance(cm=height)
    product.save()
    return product


def _gift_promotion(gift):
    promotion = PromotionFactory(
        trigger=PromotionTrigger.AUTOMATIC,
        benefit_type=BenefitType.FREE_GIFT,
        get_quantity=1,
    )
    promotion.get_products.add(gift)
    return promotion


def test_gift_pairs_are_the_entitled_gifts():
    gift = _product(cm=(30, 20, 6))
    _gift_promotion(gift)
    cart = CartFactory(user=None, num_cart_items=0)
    CartItemFactory(cart=cart, product=_product(cm=(30, 20, 5)), quantity=1)

    result = PromotionEngine.evaluate(cart)

    assert PromotionEngine.gift_pairs(result) == [(gift, 1)]


class TestOptions:
    @staticmethod
    def _flag(cart):
        ShippingProvider.objects.filter(code="boxnow").update(is_active=True)
        response = APIClient().get(
            reverse("shipping-options"),
            {"country_code": "GR"},
            HTTP_X_CART_ID=str(cart.uuid),
        )
        assert response.status_code == status.HTTP_200_OK
        return next(
            o["exceedsParcelSize"]
            for o in response.json()
            if o["providerCode"] == "boxnow"
        )

    def _cart(self):
        # 30 cm tall lying flat: fits Large (36) alone, not with a 10 cm gift
        # on top.
        cart = CartFactory(user=None, num_cart_items=0)
        CartItemFactory(
            cart=cart, product=_product(cm=(45, 40, 30)), quantity=1
        )
        return cart

    def test_a_gift_that_pushes_the_cart_past_the_largest_compartment(self):
        cart = self._cart()
        assert self._flag(cart) is False

        _gift_promotion(_product(cm=(30, 20, 10)))

        assert self._flag(cart) is True

    def test_a_gift_the_engine_excludes_does_not_count(self):
        """A sold-out gift is never entitled, so it adds nothing."""
        cart = self._cart()
        _gift_promotion(_product(cm=(30, 20, 10), stock=0))

        assert self._flag(cart) is False


class TestOrderCreation:
    @pytest.fixture
    def checkout(self):
        user = UserAccountFactory()
        country = CountryFactory()
        enable_rate(country, provider_code="boxnow", kind="pickup_point")
        ShippingProvider.objects.filter(code="boxnow").update(is_active=True)
        BoxNowLockerFactory(external_id="4", country_code=country.alpha_2)
        cart = CartFactory(user=user)
        cart.items.all().delete()
        # 5 cm tall alone: Small (8).
        CartItemFactory(cart=cart, product=_product(cm=(30, 20, 5)), quantity=1)
        return {
            "user": user,
            "cart": cart,
            "pay_way": PayWayFactory(
                settlement=PaySettlement.OFFLINE_TRANSFER,
                cost=Money(Decimal(0), "EUR"),
                free_threshold=Money(Decimal(0), "EUR"),
            ),
            "shipping_address": {
                "first_name": "Eleni",
                "last_name": "Georgiou",
                "email": "eleni@example.com",
                "street": "Akadimias",
                "street_number": "3",
                "city": "Athens",
                "zipcode": "10671",
                "country_id": country.alpha_2,
                "phone": "+306900000002",
                "shipping_provider_code": "boxnow",
                "shipping_kind": "pickup_point",
                "boxnow_locker_id": "4",
            },
        }

    def _place(self, checkout):
        order = OrderService.create_order_from_cart_offline(
            cart=checkout["cart"],
            shipping_address=checkout["shipping_address"],
            pay_way=checkout["pay_way"],
            user=checkout["user"],
        )
        return BoxNowShipment.objects.get(order=order).compartment_size

    def test_the_cart_alone_fits_small(self, checkout):
        assert self._place(checkout) == BoxNowCompartmentSize.SMALL

    def test_a_gift_raises_the_booked_compartment(self, checkout):
        # 5 + 6 cm stacked: Medium.
        _gift_promotion(_product(cm=(30, 20, 6)))

        assert self._place(checkout) == BoxNowCompartmentSize.MEDIUM

    def test_a_sold_out_gift_does_not(self, checkout):
        _gift_promotion(_product(cm=(30, 20, 6), stock=0))

        assert self._place(checkout) == BoxNowCompartmentSize.SMALL
