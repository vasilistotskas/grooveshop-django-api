"""``potentialPoints``: what one unit of a product earns at the store's base
rate, on the product card and the product page.

The number is ``LoyaltyService.calculate_item_points(product, 1)`` and
nothing else, so these tests state the expected figure by hand for each
price basis, the coefficient and the flat points, and also check it equals
the service's own answer. A signed-in shopper's tier-adjusted number is the
loyalty endpoint's: the tier multiplier is not part of this one.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from loyalty.enum import PriceBasis
from loyalty.services import LoyaltyService
from product.factories.product import ProductFactory
from tests.utils.loyalty import loyalty_settings
from tests.utils.openapi import assert_matches_response
from vat.factories import VatFactory

pytestmark = pytest.mark.django_db


def _product(**kwargs):
    defaults = {
        "price": Decimal("100.00"),
        "discount_percent": Decimal(10),
        "vat": VatFactory(value=24),
        "active": True,
        "stock": 5,
        "points_coefficient": Decimal("1.00"),
        "points": 0,
        "num_images": 0,
        "num_reviews": 0,
    }
    return ProductFactory(**{**defaults, **kwargs})


def _detail(product) -> dict:
    response = APIClient().get(reverse("product-detail", args=[product.pk]))
    assert response.status_code == status.HTTP_200_OK
    return response.json()


def _card(product) -> dict:
    response = APIClient().get(reverse("product-list"))
    assert response.status_code == status.HTTP_200_OK
    return next(r for r in response.json()["results"] if r["id"] == product.pk)


# price 100.00, 10% markdown, 24% VAT:
#   without discount, excluding VAT   100
#   with discount, excluding VAT       90
#   without discount, including VAT   124
#   final price (90 + 24% of 90)     111.6
@pytest.mark.parametrize(
    ("basis", "expected"),
    [
        (PriceBasis.PRICE_EXCL_VAT_NO_DISCOUNT, 100),
        (PriceBasis.PRICE_EXCL_VAT_WITH_DISCOUNT, 90),
        (PriceBasis.PRICE_INCL_VAT_NO_DISCOUNT, 124),
        (PriceBasis.FINAL_PRICE, 111),
    ],
)
def test_each_price_basis(basis, expected):
    product = _product()

    with loyalty_settings(LOYALTY_PRICE_BASIS=basis, LOYALTY_POINTS_FACTOR=1):
        assert _detail(product)["potentialPoints"] == expected
        assert _card(product)["potentialPoints"] == expected


def test_the_points_factor_scales_the_basis():
    product = _product()

    with loyalty_settings(
        LOYALTY_PRICE_BASIS=PriceBasis.PRICE_EXCL_VAT_NO_DISCOUNT,
        LOYALTY_POINTS_FACTOR=0.5,
    ):
        assert _detail(product)["potentialPoints"] == 50


def test_the_products_coefficient_scales_it():
    product = _product(points_coefficient=Decimal("2.50"))

    with loyalty_settings(
        LOYALTY_PRICE_BASIS=PriceBasis.PRICE_EXCL_VAT_NO_DISCOUNT,
        LOYALTY_POINTS_FACTOR=1,
    ):
        assert _detail(product)["potentialPoints"] == 250


def test_a_zero_coefficient_leaves_only_the_flat_points():
    product = _product(points_coefficient=Decimal(0), points=15)

    with loyalty_settings(LOYALTY_POINTS_FACTOR=1):
        assert _detail(product)["potentialPoints"] == 15
        assert _card(product)["potentialPoints"] == 15


def test_flat_points_add_to_the_calculated_ones():
    product = _product(points=7)

    with loyalty_settings(
        LOYALTY_PRICE_BASIS=PriceBasis.PRICE_EXCL_VAT_NO_DISCOUNT,
        LOYALTY_POINTS_FACTOR=1,
    ):
        assert _detail(product)["potentialPoints"] == 107


def test_the_tier_multiplier_is_not_part_of_it():
    product = _product()

    with loyalty_settings(
        LOYALTY_PRICE_BASIS=PriceBasis.PRICE_EXCL_VAT_NO_DISCOUNT,
        LOYALTY_POINTS_FACTOR=1,
        LOYALTY_TIER_MULTIPLIER_ENABLED=True,
    ):
        assert _detail(product)["potentialPoints"] == 100


def test_it_is_zero_when_loyalty_is_off():
    product = _product(points=7)

    with loyalty_settings(LOYALTY_ENABLED=False):
        assert _detail(product)["potentialPoints"] == 0
        assert _card(product)["potentialPoints"] == 0


def test_it_is_the_services_own_answer():
    product = _product(points_coefficient=Decimal("1.30"), points=3)

    with loyalty_settings():
        served = _detail(product)["potentialPoints"]

        assert served == LoyaltyService.calculate_item_points(product, 1)


def test_the_list_does_not_query_once_per_product():
    def queries(count: int) -> int:
        for _ in range(count):
            _product()
        with CaptureQueriesContext(connection) as context:
            response = APIClient().get(reverse("product-list"))
        assert response.status_code == status.HTTP_200_OK
        return len(context)

    few = queries(2)
    many = queries(6)

    assert many - few <= 1


def test_the_responses_satisfy_the_published_schema(openapi_schema):
    product = _product(points=3)

    with loyalty_settings():
        list_response = APIClient().get(reverse("product-list"))
        assert_matches_response(
            openapi_schema, "/api/v1/product", list_response.json()
        )
        assert_matches_response(
            openapi_schema,
            "/api/v1/product/{id}",
            _detail(product),
        )
