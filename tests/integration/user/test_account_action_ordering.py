"""``?ordering=`` on the account sub-resource listings.

Every account page on the storefront (favourites, reviews, addresses,
notifications, liked posts) renders a sort control; until 2026-09-18 the
actions behind them blanked ``ordering_fields`` and the choice changed
nothing. Each action now declares the columns of the model it lists.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from product.factories.favourite import ProductFavouriteFactory
from product.factories.product import ProductFactory
from product.models.favourite import ProductFavourite
from user.factories import UserAccountFactory
from user.factories.address import UserAddressFactory


def _ids(response) -> list[int]:
    assert response.status_code == 200, response.content
    return [row["id"] for row in response.json()["results"]]


@pytest.fixture
def user():
    return UserAccountFactory()


@pytest.fixture
def client(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.mark.django_db
def test_favourite_products_sort_both_ways(client, user):
    older = ProductFavouriteFactory(user=user, product=ProductFactory())
    newer = ProductFavouriteFactory(user=user, product=ProductFactory())
    ProductFavourite.objects.filter(pk=older.pk).update(
        created_at=timezone.now() - timedelta(days=2)
    )
    url = reverse("user-account-favourite-products", kwargs={"pk": user.pk})

    assert _ids(client.get(url, {"ordering": "createdAt"})) == [
        older.pk,
        newer.pk,
    ]
    assert _ids(client.get(url, {"ordering": "-createdAt"})) == [
        newer.pk,
        older.pk,
    ]
    # The USER's columns are not this listing's; they are dropped, not 400.
    assert _ids(client.get(url, {"ordering": "email"})) == [newer.pk, older.pk]


@pytest.mark.django_db
def test_addresses_default_to_main_first_and_honour_a_sort(client, user):
    secondary = UserAddressFactory(user=user, is_main=False)
    main = UserAddressFactory(user=user, is_main=True)
    url = reverse("user-account-addresses", kwargs={"pk": user.pk})

    assert _ids(client.get(url))[0] == main.pk
    assert _ids(client.get(url, {"ordering": "isMain"}))[0] == secondary.pk
