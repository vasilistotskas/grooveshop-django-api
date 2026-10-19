"""A retried cart add is made once, from whichever address it arrives.

The agent gateway runs several pods, and a chat turn the shopper retries
can reach Django from another one than the first attempt; a phone can
change networks between a checkout and its retry. The key used to be
scoped to the address, so such a retry ran again. Through the real
middleware chain and view (``core/middleware/idempotency.py``).
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from django.urls import reverse
from knox.models import get_token_model
from rest_framework import status
from rest_framework.test import APIClient

from cart.factories import CartFactory
from product.factories import ProductFactory
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

AuthToken = get_token_model()


def _product():
    return ProductFactory(
        price=Decimal("10.00"), stock=50, active=True, discount_percent=0
    )


def _add_twice(client: APIClient, product, **headers: str):
    """The add, then its retry from another address."""
    headers["HTTP_IDEMPOTENCY_KEY"] = str(uuid4())

    def add(address: str):
        return client.post(
            reverse("cart-item-list"),
            {"product": product.pk, "quantity": 1},
            format="json",
            REMOTE_ADDR=address,
            **headers,
        )

    first = add("10.42.0.11")
    assert first.status_code == status.HTTP_201_CREATED, first.content
    return add("10.42.1.12")


def test_a_guest_cart_add_retried_from_another_address_adds_once():
    cart = CartFactory(is_guest=True, num_cart_items=0)
    product = _product()

    retry = _add_twice(APIClient(), product, HTTP_X_CART_ID=str(cart.uuid))

    assert retry["Idempotent-Replay"] == "true"
    assert cart.items.get(product=product).quantity == 1


def test_a_signed_in_add_retried_from_another_address_adds_once():
    user = UserAccountFactory(num_addresses=0)
    cart = CartFactory(user=user, num_cart_items=0)
    product = _product()
    _token, raw = AuthToken.objects.create(user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")

    retry = _add_twice(client, product)

    assert retry["Idempotent-Replay"] == "true"
    assert cart.items.get(product=product).quantity == 1
