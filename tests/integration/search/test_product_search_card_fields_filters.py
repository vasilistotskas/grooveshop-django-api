"""Card fields on product search hits, and the brand / in-stock / on-offer
filters (Groove Volt F1, F2).

The card fields are hydrated from the database, so they are checked
without an engine. The filters are checked against the live engine.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from product.enum.review import ReviewStatus
from product.factories.brand import BrandFactory
from product.factories.product import ProductFactory
from product.factories.review import ProductReviewFactory
from product.models.product import ProductTranslation
from search.serializers import ProductTranslationSerializer
from tests.conftest import requires_meilisearch
from tests.utils.meilisearch import LiveSearchIndex, unique_marker
from user.factories import UserAccountFactory


def _hits(products):
    qs = ProductTranslation.get_search_result_queryset().filter(
        master__in=products, language_code="en"
    )
    return {
        row["master"]: row
        for row in (
            ProductTranslationSerializer(obj, context={}).data for obj in qs
        )
    }


@pytest.mark.django_db
class TestCardFields:
    def test_hit_carries_brand_review_count_created_at_and_threshold(self):
        brand = BrandFactory(name="Acme Audio")
        product = ProductFactory(
            brand=brand, stock=3, low_stock_threshold=5, num_reviews=0
        )
        row = _hits([product])[product.id]

        assert row["brand_name"] == "Acme Audio"
        assert row["low_stock_threshold"] == 5
        assert row["stock"] == 3
        assert row["created_at"] is not None

    def test_brandless_product_has_null_brand_name(self):
        product = ProductFactory(brand=None, num_reviews=0)
        assert _hits([product])[product.id]["brand_name"] is None

    def test_review_count_is_approved_only(self):
        product = ProductFactory(num_reviews=0)
        for status_value in (
            ReviewStatus.TRUE,
            ReviewStatus.TRUE,
            ReviewStatus.NEW,
            ReviewStatus.FALSE,
        ):
            ProductReviewFactory(
                product=product,
                user=UserAccountFactory(),
                status=status_value,
            )

        assert _hits([product])[product.id]["review_count"] == 2

    def test_hydration_stays_constant_query(self):
        small = ProductFactory.create_batch(
            2, brand=BrandFactory(), num_reviews=1
        )
        with CaptureQueriesContext(connection) as few:
            _hits(small)

        many = small + ProductFactory.create_batch(
            3, brand=BrandFactory(), num_reviews=1
        )
        with CaptureQueriesContext(connection) as lots:
            _hits(many)

        assert len(few) == len(lots)


@pytest.mark.django_db
class TestFilterParamValidation:
    @pytest.mark.parametrize(
        "params",
        [{"brands": "1,x"}, {"in_stock": "ture"}, {"on_offer": "maybe"}],
    )
    def test_garbage_is_a_400(self, params):
        response = APIClient().get(
            reverse("search-product"), {**params, "query": "a"}
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@requires_meilisearch
@pytest.mark.django_db
class TestFilters:
    @pytest.fixture
    def catalogue(self, db):
        marker = unique_marker()
        acme, other = BrandFactory(), BrandFactory()

        def make(**kwargs):
            product = ProductFactory(
                active=True,
                num_reviews=0,
                translations=[
                    ProductTranslation(language_code="en", name=marker)
                ],
                **kwargs,
            )
            return product, product.translations.get()

        with LiveSearchIndex() as index:
            rows = {
                "acme_sale": make(
                    brand=acme, stock=5, discount_percent=Decimal(10)
                ),
                "acme_sold_out": make(
                    brand=acme, stock=0, discount_percent=Decimal(0)
                ),
                "other_plain": make(
                    brand=other, stock=5, discount_percent=Decimal(0)
                ),
                "no_brand": make(
                    brand=None, stock=5, discount_percent=Decimal(0)
                ),
            }
            index.add(*(translation for _, translation in rows.values()))
            yield (
                marker,
                acme,
                {key: product.id for key, (product, _) in rows.items()},
            )

    def _masters(self, marker, **params):
        response = APIClient().get(
            reverse("search-product"),
            {"query": marker, "language_code": "en", **params},
        )
        assert response.status_code == status.HTTP_200_OK
        return {row["master"] for row in response.json()["results"]}

    def test_brand_filter(self, catalogue):
        marker, acme, ids = catalogue
        assert self._masters(marker, brands=str(acme.id)) == {
            ids["acme_sale"],
            ids["acme_sold_out"],
        }

    def test_in_stock_filter(self, catalogue):
        marker, _, ids = catalogue
        assert self._masters(marker, in_stock="true") == set(ids.values()) - {
            ids["acme_sold_out"]
        }

    def test_on_offer_filter(self, catalogue):
        marker, _, ids = catalogue
        assert self._masters(marker, on_offer="true") == {ids["acme_sale"]}

    def test_filters_compose_and_false_means_unfiltered(self, catalogue):
        marker, acme, ids = catalogue
        assert self._masters(
            marker, brands=str(acme.id), in_stock="true", on_offer="true"
        ) == {ids["acme_sale"]}
        assert self._masters(marker, in_stock="false", on_offer="0") == set(
            ids.values()
        )
