"""A shopper's favourite products, for an account without a username.

``username`` is optional on the account (``null=True``) — a shopper who
signed up by email has none — so the favourite's ``user_username`` is
``null`` for them. It was declared a plain string, which made the
storefront's generated schema refuse the whole list for every such
shopper: their favourites page never loaded.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from product.factories.favourite import ProductFavouriteFactory
from product.factories.product import ProductFactory
from product.serializers.favourite import ProductFavouriteSerializer
from user.factories import UserAccountFactory


@pytest.mark.django_db
def test_lists_the_favourites_of_a_shopper_without_a_username():
    user = UserAccountFactory(username=None)
    ProductFavouriteFactory(user=user, product=ProductFactory())
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.get(
        reverse("user-account-favourite-products", kwargs={"pk": user.pk})
    )

    assert response.status_code == 200, response.content
    assert [row["userUsername"] for row in response.json()["results"]] == [None]


def test_declares_the_username_nullable_in_the_contract():
    # What the OpenAPI schema (and the storefront's generated Zod) reads.
    assert ProductFavouriteSerializer().fields["user_username"].allow_null
