"""What the cart actions cost, and what the add response carries.

Add to cart is the slowest click on the storefront, and the page then
read the cart again. These pin the query counts of the two calls, and
that the response carries the cart a ``GET /cart`` would return.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from cart.factories import CartFactory, CartItemFactory
from country.factories import CountryFactory
from product.factories import ProductFactory
from shipping.enum import ShippingKind
from shipping.models import ShippingProvider
from shipping_acs.factories import AcsStationFactory
from tests.utils.shipping import enable_rate
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

# Measured on the factory data below (a cart of two lines), with a
# one-query allowance for the tenant artifacts a cold cache adds. Neither
# response computes product recommendations (the storefront asks the
# recommendations endpoint for them), which was most of both. Each product
# serializer reads the loyalty rates once, however many lines it draws: a query
# here, where the rows are absent; one cache read in production. Wholesale
# pricing, loyalty and the reviews each read the customer accounts switch
# they need as well (``tenant.features``): queries here, cache reads there.
GET_CART_QUERIES = 32
ADD_ITEM_QUERIES = 54


def _product():
    return ProductFactory(
        price=Decimal("10.00"),
        stock=50,
        active=True,
        discount_percent=0,
        num_images=2,
        num_reviews=2,
    )


def _queries(call) -> tuple[int, object]:
    call()  # warm the caches that are not part of the cost
    with CaptureQueriesContext(connection) as captured:
        response = call()
    return len(captured), response


@pytest.fixture
def shopper():
    user = UserAccountFactory(num_addresses=0)
    cart = CartFactory(user=user, num_cart_items=0)
    for _ in range(2):
        CartItemFactory(cart=cart, product=_product(), quantity=1)
    client = APIClient()
    client.force_authenticate(user)
    return user, cart, client


class TestQueryBudget:
    def test_get_cart(self, shopper):
        _user, _cart, client = shopper

        count, response = _queries(lambda: client.get(reverse("cart-detail")))

        assert response.status_code == status.HTTP_200_OK
        assert count <= GET_CART_QUERIES + 1, count

    def test_the_stores_shipping_countries_cost_no_query_each(
        self, shopper, acs_configured_tenant
    ):
        # The cart's free-shipping line is judged for the first shippable
        # country, and a country is shippable only where its carrier passes
        # the quote's gate: ACS lockers need stations there. That is one
        # station query for every country, not one per country.
        _user, _cart, client = shopper
        ShippingProvider.objects.filter(code="acs").update(is_active=True)

        def lockers_in(country):
            enable_rate(
                country, provider_code="acs", kind=ShippingKind.PICKUP_POINT
            )
            AcsStationFactory(country_code=country.alpha_2)

        lockers_in(CountryFactory(num_regions=0))
        one, _response = _queries(lambda: client.get(reverse("cart-detail")))
        for _ in range(3):
            lockers_in(CountryFactory(num_regions=0))
        four, response = _queries(lambda: client.get(reverse("cart-detail")))

        assert response.status_code == status.HTTP_200_OK
        assert four == one, (one, four)

    def test_add_item(self, shopper):
        _user, _cart, client = shopper
        target = _product()

        count, response = _queries(
            lambda: client.post(
                reverse("cart-item-list"),
                {"product": target.pk, "quantity": 1},
                format="json",
            )
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert count <= ADD_ITEM_QUERIES + 1, count


class TestNoRecommendationsInTheCartPayloads:
    def test_cart_and_lines_carry_none(self, shopper):
        _user, cart, client = shopper
        line = cart.items.first()

        bodies = [
            client.get(reverse("cart-detail")).json(),
            client.get(reverse("cart-item-detail", args=[line.pk])).json(),
            client.post(
                reverse("cart-item-list"),
                {"product": _product().pk, "quantity": 1},
                format="json",
            ).json(),
        ]

        for body in bodies:
            assert "recommendations" not in body
        assert "recommendations" not in bodies[2]["cart"]


class TestAddResponseCarriesTheCart:
    def test_create_returns_the_cart_a_get_returns(self, shopper):
        _user, _cart, client = shopper
        target = _product()

        created = client.post(
            reverse("cart-item-list"),
            {"product": target.pk, "quantity": 2},
            format="json",
        )
        fetched = client.get(reverse("cart-detail"))

        assert created.status_code == status.HTTP_201_CREATED, created.json()
        body = created.json()
        assert body["product"]["id"] == target.pk
        assert body["quantity"] == 2
        embedded = body["cart"]
        expected = fetched.json()
        assert {line["id"] for line in embedded["items"]} == {
            line["id"] for line in expected["items"]
        }
        assert len(embedded["items"]) == 3
        assert embedded["totalPrice"] == expected["totalPrice"]
        assert embedded["totalItems"] == expected["totalItems"]

    def test_update_returns_the_cart_with_the_new_quantity(self, shopper):
        _user, cart, client = shopper
        line = cart.items.first()

        response = client.patch(
            reverse("cart-item-detail", args=[line.pk]),
            {"quantity": 3},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        embedded = response.json()["cart"]
        quantities = {
            item["id"]: item["quantity"] for item in embedded["items"]
        }
        assert quantities[line.pk] == 3

    def test_a_guest_add_returns_the_guest_cart(self):
        cart = CartFactory(is_guest=True, num_cart_items=0)

        response = APIClient().post(
            reverse("cart-item-list"),
            {"product": _product().pk, "quantity": 1},
            format="json",
            HTTP_X_CART_ID=str(cart.uuid),
        )

        assert response.status_code == status.HTTP_201_CREATED, response.json()
        assert response.json()["cart"]["uuid"] == str(cart.uuid)
        assert len(response.json()["cart"]["items"]) == 1
