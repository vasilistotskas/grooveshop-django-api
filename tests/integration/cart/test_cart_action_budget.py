"""What the cart actions cost, and what the add response carries.

Add to cart is the slowest click on the storefront, and the page then
read the cart again. These pin the query counts of the two calls, and
that the response carries the cart a ``GET /cart`` would return.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from cart.factories import CartFactory, CartItemFactory
from cart.models import Cart
from cart.models.cart import LAST_ACTIVITY_WRITE_INTERVAL
from product.factories import ProductFactory
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

# Measured on the factory data below (a cart of two lines), with a
# one-query allowance for the tenant artifacts a cold cache adds. The add
# is dearer than the cart read because its response carries the cart the
# shopper no longer has to fetch, plus the line's own ``recommendations``
# until the storefront stops requiring that field.
GET_CART_QUERIES = 38
ADD_ITEM_QUERIES = 59


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


class TestLastActivity:
    def test_a_read_inside_the_interval_does_not_write(self, shopper):
        _user, cart, client = shopper
        before = Cart.objects.get(pk=cart.pk).last_activity

        client.get(reverse("cart-detail"))

        assert Cart.objects.get(pk=cart.pk).last_activity == before

    def test_a_read_after_the_interval_records_the_visit(self, shopper):
        _user, cart, client = shopper
        stale = (
            timezone.now() - LAST_ACTIVITY_WRITE_INTERVAL - timedelta(minutes=1)
        )
        Cart.objects.filter(pk=cart.pk).update(last_activity=stale)

        client.get(reverse("cart-detail"))

        assert Cart.objects.get(pk=cart.pk).last_activity > stale
