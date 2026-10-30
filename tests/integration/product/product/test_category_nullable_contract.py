"""A product's ``category`` may be null, and the schema must say so.

``Product.category`` is ``null=True, blank=True, on_delete=SET_NULL``: the
admin saves a product without one, and deleting a category leaves its
products with none. ``ProductSerializer`` declared the field as a bare
``PrimaryKeyRelatedField``, which does not inherit the model's
nullability, so ``Product``, ``ProductDetail``, ``ProductRetrieve`` and
``ProductDetailResponse`` published ``category`` as a non-null integer.
The storefront generates its Zod schemas from that document and answers
422 to a body they reject, so one uncategorised product broke the product
page and every cart, order and favourite list nesting it.

``ProductWriteSerializer`` required one too, so a product in that state
could not be saved back through the API with its own payload.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from product.factories.category import ProductCategoryFactory
from product.factories.product import ProductFactory
from product.serializers.favourite import ProductDetailResponseSerializer
from product.serializers.product import (
    ProductCardSerializer,
    ProductDetailSerializer,
    ProductRetrieveSerializer,
    ProductSerializer,
    ProductWriteSerializer,
)
from tests.utils.openapi import assert_matches_response

PRODUCT_COMPONENTS = (
    "Product",
    "ProductCard",
    "ProductDetail",
    "ProductRetrieve",
    "ProductDetailResponse",
)
READ_SERIALIZERS = (
    ProductSerializer,
    ProductCardSerializer,
    ProductDetailSerializer,
    ProductRetrieveSerializer,
    ProductDetailResponseSerializer,
)


@pytest.fixture
def uncategorised_product(db):
    """A product whose category was deleted: SET_NULL leaves it with none."""
    category = ProductCategoryFactory()
    product = ProductFactory(category=category, active=True)
    category.delete()
    product.refresh_from_db()
    assert product.category is None, "sanity: SET_NULL cleared it"
    return product


@pytest.mark.parametrize("component", PRODUCT_COMPONENTS)
def test_category_is_nullable_in_the_published_schema(
    openapi_schema, component
):
    schema = openapi_schema["components"]["schemas"][component]

    assert schema["properties"]["category"].get("nullable") is True
    # Still always present: null is the only new value, so a storefront
    # reading it needs no default.
    assert "category" in schema["required"]


@pytest.mark.parametrize("serializer", READ_SERIALIZERS)
def test_an_uncategorised_product_serialises_null(
    uncategorised_product, serializer
):
    assert serializer(uncategorised_product).data["category"] is None


@pytest.mark.parametrize(
    ("route", "path", "args"),
    [
        ("product-detail", "/api/v1/product/{id}", True),
        ("product-list", "/api/v1/product", False),
        ("product-feed", "/api/v1/product/feed", False),
    ],
)
def test_the_response_validates_against_the_schema(
    openapi_schema, uncategorised_product, route, path, args
):
    url = reverse(route, args=[uncategorised_product.pk] if args else None)

    response = APIClient().get(url)

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    rows = [body] if args else body["results"]
    assert [row["category"] for row in rows] == [None]
    assert_matches_response(openapi_schema, path, body)


@pytest.mark.django_db
def test_the_write_serializer_accepts_no_category():
    product = ProductFactory()

    serializer = ProductWriteSerializer(
        product, data={"category": None}, partial=True
    )

    assert serializer.is_valid(), serializer.errors
    assert serializer.save().category is None


def test_the_write_schema_does_not_require_a_category(openapi_schema):
    schema = openapi_schema["components"]["schemas"]["ProductWriteRequest"]

    assert schema["properties"]["category"].get("nullable") is True
    assert "category" not in schema["required"]
