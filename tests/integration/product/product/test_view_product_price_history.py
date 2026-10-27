"""``GET /api/v1/product/{id}/price-history``: the recorded consumer price."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from core.cache.registry import get_surface
from product.factories.product import ProductFactory
from product.models.price_spell import ProductPriceSpell
from tests.utils.store_settings import switch_store_settings

pytestmark = pytest.mark.django_db


def _url(pk) -> str:
    return reverse("product-price-history", args=[pk])


def _spells(product, *prices_and_days_ago):
    """Closed spells for each ``(price, days_ago)`` but the last, which is
    open. Replaces whatever the factory recorded."""
    ProductPriceSpell.objects.filter(product=product).delete()
    now = timezone.now()
    last = len(prices_and_days_ago) - 1
    for index, (price, days_ago) in enumerate(prices_and_days_ago):
        valid_to = (
            None
            if index == last
            else now - timedelta(days=prices_and_days_ago[index + 1][1])
        )
        ProductPriceSpell.objects.create(
            product=product,
            price=Decimal(price),
            valid_from=now - timedelta(days=days_ago),
            valid_to=valid_to,
        )


def test_spells_are_ordered_oldest_first_with_an_open_current_spell():
    product = ProductFactory(active=True)
    _spells(product, ("30.00", 90), ("25.00", 40), ("20.00", 5))

    response = APIClient().get(_url(product.pk))

    assert response.status_code == status.HTTP_200_OK
    spells = response.json()["spells"]
    assert [Decimal(str(s["price"])) for s in spells] == [
        Decimal("30.00"),
        Decimal("25.00"),
        Decimal("20.00"),
    ]
    assert [s["validTo"] is None for s in spells] == [False, False, True]
    assert spells[0]["validTo"] == spells[1]["validFrom"]
    assert set(spells[0]) == {"validFrom", "validTo", "price"}


def test_currency_is_the_store_currency():
    product = ProductFactory(active=True)

    response = APIClient().get(_url(product.pk))

    assert response.json()["currency"] == settings.DEFAULT_CURRENCY


def test_a_product_with_no_spells_serves_an_empty_list():
    product = ProductFactory(active=True)
    ProductPriceSpell.objects.filter(product=product).delete()

    response = APIClient().get(_url(product.pk))

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["spells"] == []


def test_another_products_spells_are_not_served():
    product = ProductFactory(active=True)
    other = ProductFactory(active=True)
    _spells(product, ("10.00", 10))
    _spells(other, ("99.00", 10), ("98.00", 3))

    spells = APIClient().get(_url(product.pk)).json()["spells"]

    assert len(spells) == 1


def test_an_unknown_product_is_404():
    assert APIClient().get(_url(10**9)).status_code == (
        status.HTTP_404_NOT_FOUND
    )


def test_an_inactive_product_is_404():
    product = ProductFactory(active=False)

    assert APIClient().get(_url(product.pk)).status_code == (
        status.HTTP_404_NOT_FOUND
    )


def test_a_soft_deleted_product_is_404():
    product = ProductFactory(active=True)
    product.delete()

    assert APIClient().get(_url(product.pk)).status_code == (
        status.HTTP_404_NOT_FOUND
    )


def test_it_is_404_when_the_feature_is_off():
    product = ProductFactory(active=True)
    switch_store_settings(PRICE_HISTORY_ENABLED=False)

    assert APIClient().get(_url(product.pk)).status_code == (
        status.HTTP_404_NOT_FOUND
    )


def test_it_is_on_by_default():
    product = ProductFactory(active=True)

    assert APIClient().get(_url(product.pk)).status_code == status.HTTP_200_OK


def test_the_query_count_does_not_grow_with_the_spells(
    django_assert_max_num_queries,
):
    product = ProductFactory(active=True)
    _spells(product, *[(f"{10 + i}.00", 100 - i) for i in range(30)])

    # Two store-setting reads per gate (catalogue, price history).
    with django_assert_max_num_queries(6):
        response = APIClient().get(_url(product.pk))

    assert len(response.json()["spells"]) == 30


def test_the_products_surface_purges_the_storefront_handler():
    assert (
        "cache:nitro:handlers:product-price-history*"
        in get_surface("products").nuxt_patterns
    )
