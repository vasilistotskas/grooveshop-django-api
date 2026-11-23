"""Price filter and price sort follow what the customer pays.

``Product.effective_price`` is the unit price the cart charges, with the
unconditional automatic promotions in it. The product list API and the
Meilisearch document both filter and sort on it; a conditional promotion
(a minimum quantity here) changes nothing, because the cart only honours
it for a basket that meets the condition.

Prices: 100.00 net at 24% VAT is 124.00 gross, 99.20 under a 20% promotion.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from extra_settings.models import Setting
from rest_framework import status
from rest_framework.test import APIClient

from product.factories import ProductFactory
from product.factories.category import ProductCategoryFactory
from product.models.product import ProductTranslation
from promotion.enum import BenefitType, PromotionTrigger, TargetScope
from promotion.factories.promotion import PromotionFactory
from tests.conftest import requires_meilisearch
from tests.utils.meilisearch import LiveSearchIndex, unique_marker
from vat.models import Vat


@pytest.fixture(autouse=True)
def promotions_on():
    original = Setting.get.__func__

    def _get(cls, key, default=None):
        if key == "PROMOTIONS_ENABLED":
            return True
        return original(cls, key, default)

    with patch.object(Setting, "get", classmethod(_get)):
        cache.clear()
        yield
        cache.clear()


@pytest.fixture
def vat(db):
    return Vat.objects.get_or_create(value=Decimal("24.0"))[0]


def make_product(vat, category, *, price="100.00", **kwargs):
    return ProductFactory(
        category=category,
        vat=vat,
        price=Decimal(price),
        discount_percent=Decimal(0),
        active=True,
        num_reviews=0,
        **kwargs,
    )


def category_promotion(category, **kwargs):
    kwargs.setdefault("trigger", PromotionTrigger.AUTOMATIC)
    kwargs.setdefault("target_scope", TargetScope.CATEGORIES)
    kwargs.setdefault("benefit_type", BenefitType.PERCENTAGE)
    kwargs.setdefault("benefit_value", Decimal(20))
    kwargs.setdefault("is_active", True)
    offer = PromotionFactory(**kwargs)
    offer.categories.set([category])
    return offer


def build_catalogue(vat, *, marker=None):
    """Three products, each in its own category.

    * ``promoted``: 124.00 listed, 99.20 under a 20% category promotion.
    * ``plain``: 111.60, no promotion.
    * ``conditional``: 124.00, under a 20% promotion that needs two units.

    With a ``marker`` each product gets one English translation named by it,
    so a live search can find exactly these three.
    """

    def product(category, **kwargs):
        if marker is not None:
            # One instance per product: a translation has one master.
            kwargs["translations"] = [
                ProductTranslation(language_code="en", name=marker)
            ]
        return make_product(vat, category, **kwargs)

    promoted_category = ProductCategoryFactory()
    conditional_category = ProductCategoryFactory()
    products = {
        "promoted": product(promoted_category),
        "plain": product(ProductCategoryFactory(), price="90.00"),
        "conditional": product(conditional_category),
    }
    category_promotion(promoted_category)
    category_promotion(conditional_category, min_quantity=2)
    return products


@pytest.fixture
def catalogue(vat):
    return build_catalogue(vat)


@pytest.mark.django_db
class TestProductListApi:
    url = "product-list"

    def _get(self, **params):
        response = APIClient().get(reverse(self.url), params)
        assert response.status_code == status.HTTP_200_OK
        return [row["id"] for row in response.json()["results"]]

    def test_the_promotion_is_recorded_in_the_effective_price(self, catalogue):
        for key, expected in (
            ("promoted", "99.20"),
            ("plain", "111.60"),
            ("conditional", "124.00"),
        ):
            catalogue[key].refresh_from_db()
            assert catalogue[key].effective_price == Decimal(expected)

    def test_a_product_under_a_category_promotion_is_found_at_its_promoted_price(
        self, catalogue
    ):
        ids = self._get(maxEffectivePrice="100")

        assert ids == [catalogue["promoted"].id]

    def test_the_promoted_price_bounds_are_inclusive(self, catalogue):
        ids = self._get(minEffectivePrice="99.20", maxEffectivePrice="99.20")

        assert ids == [catalogue["promoted"].id]

    def test_min_effective_price_leaves_out_the_promoted_product(
        self, catalogue
    ):
        ids = self._get(minEffectivePrice="100")

        assert set(ids) == {
            catalogue["plain"].id,
            catalogue["conditional"].id,
        }

    def test_a_conditional_promotion_does_not_move_the_filter(self, catalogue):
        assert catalogue["conditional"].id not in self._get(
            maxEffectivePrice="110"
        )
        assert catalogue["conditional"].id in self._get(
            minEffectivePrice="124", maxEffectivePrice="124"
        )

    def test_sorting_follows_the_effective_price(self, catalogue):
        ascending = self._get(ordering="effectivePrice")
        descending = self._get(ordering="-effectivePrice")

        # 99.20 < 111.60 < 124.00: the list price (124.00 vs 111.60)
        # would put the promoted product after the plain one.
        assert ascending == [
            catalogue["promoted"].id,
            catalogue["plain"].id,
            catalogue["conditional"].id,
        ]
        assert descending == ascending[::-1]

    def test_the_final_price_filters_are_gone_from_the_schema(
        self, openapi_schema
    ):
        names = {
            parameter["name"]
            for parameter in openapi_schema["paths"]["/api/v1/product"]["get"][
                "parameters"
            ]
        }

        assert {"minFinalPrice", "maxFinalPrice"}.isdisjoint(names)
        assert {"minEffectivePrice", "maxEffectivePrice"} <= names

    def test_the_filter_and_sort_cost_no_query_per_row(self, vat):
        category = ProductCategoryFactory()
        category_promotion(category)
        params = {
            "minEffectivePrice": "1",
            "maxEffectivePrice": "1000",
            "ordering": "-effectivePrice",
        }
        make_product(vat, category)
        client = APIClient()
        with CaptureQueriesContext(connection) as few:
            client.get(reverse(self.url), params)

        for _ in range(4):
            make_product(vat, category)
        with CaptureQueriesContext(connection) as many:
            client.get(reverse(self.url), params)

        assert len(many) == len(few)


@requires_meilisearch
@pytest.mark.django_db
class TestSearchApi:
    @pytest.fixture
    def indexed(self, vat):
        marker = unique_marker()
        # The rows are created inside the context: the engine's document
        # ids must not collide with another worker's.
        with LiveSearchIndex() as index:
            catalogue = build_catalogue(vat, marker=marker)
            index.add(
                *(product.translations.get() for product in catalogue.values())
            )
            yield marker, {key: p.id for key, p in catalogue.items()}

    def _search(self, marker, **params):
        response = APIClient().get(
            reverse("search-product"),
            {"query": marker, "language_code": "en", **params},
        )
        assert response.status_code == status.HTTP_200_OK
        return response.json()

    def _masters(self, marker, **params):
        return [
            row["master"] for row in self._search(marker, **params)["results"]
        ]

    def test_a_product_under_a_category_promotion_is_found_at_its_promoted_price(
        self, indexed
    ):
        marker, ids = indexed

        assert self._masters(marker, price_max="100") == [ids["promoted"]]

    def test_a_conditional_promotion_does_not_move_the_price_filter(
        self, indexed
    ):
        marker, ids = indexed

        assert ids["conditional"] not in self._masters(marker, price_max="110")
        assert ids["conditional"] in self._masters(
            marker, price_min="124", price_max="124"
        )

    def test_sorting_follows_the_effective_price(self, indexed):
        marker, ids = indexed

        assert self._masters(marker, sort="effectivePrice") == [
            ids["promoted"],
            ids["plain"],
            ids["conditional"],
        ]
        assert self._masters(marker, sort="-effectivePrice") == [
            ids["conditional"],
            ids["plain"],
            ids["promoted"],
        ]

    @pytest.mark.parametrize("sort", ["finalPrice", "-finalPrice"])
    def test_the_final_price_sort_is_no_longer_offered(self, indexed, sort):
        marker, _ = indexed

        # An unknown sort is ignored, so the order is the unsorted one.
        assert self._masters(marker, sort=sort) == self._masters(marker)

    def test_the_effective_price_facet_reports_the_promoted_range(
        self, indexed
    ):
        marker, _ = indexed

        stats = self._search(marker, facets="effective_price")["facetStats"]

        assert stats["effectivePrice"] == {"min": 99.2, "max": 124.0}

    def test_the_final_price_facet_is_no_longer_offered(self, indexed):
        marker, _ = indexed

        body = self._search(marker, facets="final_price")

        assert "finalPrice" not in body.get("facetStats", {})
