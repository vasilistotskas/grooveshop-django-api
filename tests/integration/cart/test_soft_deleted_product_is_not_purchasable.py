"""A soft-deleted product cannot be added to a cart or checked out.

``SoftDeleteModel.delete()`` marks the row and leaves ``active`` alone, so
a deleted product that was active stayed addable and purchasable: the
cart serializer and the checkout validation looked at ``active`` only.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from cart.factories.cart import CartFactory
from cart.factories.item import CartItemFactory
from order.services import OrderService
from product.factories.product import ProductFactory
from user.factories.account import UserAccountFactory

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]


def _deleted_product():
    product = ProductFactory(active=True, stock=10, num_images=0, num_reviews=0)
    product.delete()
    product.refresh_from_db()
    assert product.is_deleted
    assert product.active
    return product


def test_a_deleted_product_cannot_be_added_to_the_cart():
    user = UserAccountFactory(num_addresses=0)
    cart = CartFactory(user=user)
    product = _deleted_product()
    client = APIClient(HTTP_ACCEPT_LANGUAGE="en")
    client.force_authenticate(user=user)

    response = client.post(
        reverse("cart-item-list"),
        {"product": product.pk, "quantity": 1},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
    assert not cart.items.exists()


def test_a_live_product_can_still_be_added():
    user = UserAccountFactory(num_addresses=0)
    cart = CartFactory(user=user)
    product = ProductFactory(active=True, stock=10, num_images=0, num_reviews=0)
    client = APIClient(HTTP_ACCEPT_LANGUAGE="en")
    client.force_authenticate(user=user)

    response = client.post(
        reverse("cart-item-list"),
        {"product": product.pk, "quantity": 1},
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED, response.content
    assert cart.items.filter(product=product).exists()


def test_checkout_refuses_a_cart_holding_a_deleted_product():
    cart = CartFactory(user=None)
    product = ProductFactory(active=True, stock=10, num_images=0, num_reviews=0)
    CartItemFactory(cart=cart, product=product, quantity=1)
    product.delete()

    result = OrderService.validate_cart_for_checkout(cart)

    assert not result["valid"]
    assert any("no longer available" in str(e) for e in result["errors"])
