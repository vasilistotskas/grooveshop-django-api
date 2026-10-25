"""What the API and the search document say about a reduction.

Three stored columns carry it (``prior_price``, ``effective_price``,
``reduction_started_at``), so no surface may cost a query per row.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from product.factories import ProductFactory
from product.factories.category import ProductCategoryFactory
from product.models.product import ProductTranslation
from search.serializers import ProductTranslationSerializer
from vat.models import Vat

CREATED = datetime(2026, 3, 1, 10, 0, tzinfo=UTC)
MARKED_DOWN = CREATED + timedelta(days=40)


@pytest.fixture
def category(db):
    return ProductCategoryFactory()


@pytest.fixture
def vat(db):
    return Vat.objects.get_or_create(value=Decimal("24.0"))[0]


def reduced_product(category, vat):
    """An established product whose markdown started after 40 days."""
    with patch("django.utils.timezone.now", return_value=CREATED):
        product = ProductFactory(
            category=category,
            vat=vat,
            price=Decimal("100.00"),
            discount_percent=Decimal(0),
            active=True,
            num_reviews=0,
        )
    with patch("django.utils.timezone.now", return_value=MARKED_DOWN):
        product.discount_percent = Decimal(20)
        product.save()
    return product


def plain_product(category, vat):
    return ProductFactory(
        category=category,
        vat=vat,
        price=Decimal("100.00"),
        discount_percent=Decimal(0),
        active=True,
        num_reviews=0,
    )


@pytest.mark.django_db
class TestProductApi:
    def test_detail_carries_the_prior_price_and_the_announced_percent(
        self, category, vat
    ):
        product = reduced_product(category, vat)

        response = APIClient().get(
            reverse("product-detail", kwargs={"pk": product.pk})
        )

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["priorPrice"] == 124.0
        assert body["effectivePrice"] == 99.2
        # 20% off what the customer pays.
        assert body["announcedDiscountPercent"] == 20
        assert "discountPercent" not in body

    def test_detail_says_when_the_announced_reduction_began(
        self, category, vat
    ):
        product = reduced_product(category, vat)

        body = (
            APIClient()
            .get(reverse("product-detail", kwargs={"pk": product.pk}))
            .json()
        )

        assert datetime.fromisoformat(body["reductionStartedAt"]) == (
            MARKED_DOWN
        )

    def test_a_reduction_with_no_prior_price_shows_no_start(
        self, category, vat
    ):
        # ``reduction_started_at`` is also set for a reduction that
        # announces nothing; exposing it would date an announcement
        # that is not made.
        product = reduced_product(category, vat)
        assert product.reduction_started_at is not None
        type(product).objects.filter(pk=product.pk).update(prior_price=None)

        body = (
            APIClient()
            .get(reverse("product-detail", kwargs={"pk": product.pk}))
            .json()
        )

        assert body["priorPrice"] is None
        assert body["reductionStartedAt"] is None

    def test_a_product_that_is_not_reduced_announces_nothing(
        self, category, vat
    ):
        product = plain_product(category, vat)

        response = APIClient().get(
            reverse("product-detail", kwargs={"pk": product.pk})
        )

        body = response.json()
        assert body["priorPrice"] is None
        assert body["announcedDiscountPercent"] is None
        assert body["reductionStartedAt"] is None
        assert body["effectivePrice"] == 124.0

    def test_a_young_product_with_a_markdown_announces_nothing(
        self, category, vat
    ):
        product = ProductFactory(
            category=category,
            vat=vat,
            price=Decimal("100.00"),
            discount_percent=Decimal(20),
            active=True,
        )

        body = (
            APIClient()
            .get(reverse("product-detail", kwargs={"pk": product.pk}))
            .json()
        )

        assert body["priorPrice"] is None
        assert body["announcedDiscountPercent"] is None

    def test_list_filters_and_orders_by_the_announced_percent(
        self, category, vat
    ):
        announced = reduced_product(category, vat)
        plain_product(category, vat)
        # A markdown on a young product is not an announcement.
        ProductFactory(
            category=category,
            vat=vat,
            price=Decimal("100.00"),
            discount_percent=Decimal(50),
            active=True,
        )

        response = APIClient().get(
            reverse("product-list"),
            {
                "min_announced_discount_percent": 1,
                "ordering": "-announcedDiscountPercent",
            },
        )

        assert response.status_code == status.HTTP_200_OK
        rows = response.json()["results"]
        assert [row["id"] for row in rows] == [announced.pk]
        assert rows[0]["announcedDiscountPercent"] == 20

    def test_list_query_count_does_not_grow_with_the_page(self, category, vat):
        def queries_for(count):
            for _ in range(count):
                reduced_product(category, vat)
            with CaptureQueriesContext(connection) as queries:
                response = APIClient().get(reverse("product-list"))
            assert response.status_code == status.HTTP_200_OK
            rows = response.json()["results"]
            assert all(row["priorPrice"] == 124.0 for row in rows)
            return len(queries)

        assert queries_for(8) == queries_for(2)

    def test_the_schema_no_longer_carries_the_markdown_fields(
        self, openapi_schema
    ):
        properties = openapi_schema["components"]["schemas"]["Product"][
            "properties"
        ]

        for name in ("discountPercent", "discountValue", "priceSavePercent"):
            assert name not in properties
        for name in (
            "priorPrice",
            "effectivePrice",
            "announcedDiscountPercent",
            "reductionStartedAt",
        ):
            assert "deprecated" not in properties[name]

    def test_the_search_hit_schemas_no_longer_carry_the_markdown_percent(
        self, openapi_schema
    ):
        schemas = openapi_schema["components"]["schemas"]

        assert (
            "discountPercent"
            not in schemas["ProductMeiliSearchResult"]["properties"]
        )


@pytest.mark.django_db
class TestSearch:
    def test_the_index_exposes_the_effective_price_not_the_markdown_percent(
        self,
    ):
        meili = ProductTranslation.MeiliMeta

        # ``final_price`` is still displayed (the storefront falls back to
        # it) but nothing sorts on it; ``discount_percent`` stays sortable
        # for the ranking rule and is no longer displayed.
        assert "final_price" in meili.displayed_fields
        assert "final_price" not in meili.sortable_fields
        assert "effective_price" in meili.sortable_fields
        assert "discount_percent" not in meili.displayed_fields
        assert "discount_percent" in meili.sortable_fields

    def test_the_document_carries_the_two_announcement_fields(
        self, category, vat
    ):
        product = reduced_product(category, vat)
        translation = ProductTranslation.get_meilisearch_queryset().get(
            master=product, language_code="en"
        )

        document = translation.meili_serialize()

        assert document["prior_price"] == 124.0
        assert document["announced_discount_percent"] == 20

    def test_the_document_of_an_unreduced_product_has_none(self, category, vat):
        product = plain_product(category, vat)
        translation = ProductTranslation.get_meilisearch_queryset().get(
            master=product, language_code="en"
        )

        document = translation.meili_serialize()

        assert document["prior_price"] is None
        assert document["announced_discount_percent"] is None

    def test_the_card_costs_no_extra_query(self, category, vat):
        product = reduced_product(category, vat)
        hit = (
            ProductTranslation.get_search_result_queryset()
            .filter(master=product, language_code="en")
            .get()
        )
        _ = hit.master

        with CaptureQueriesContext(connection) as queries:
            data = ProductTranslationSerializer(hit, context={}).data

        assert data["prior_price"] == 124.0
        assert data["effective_price"] == 99.2
        assert data["announced_discount_percent"] == 20
        assert not any("pricespell" in q["sql"].lower() for q in queries)
