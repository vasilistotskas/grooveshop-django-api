"""What ``sidebarFacets`` asks of the engine, and what counts as a search.

Neither needs a live engine: the search is stubbed and the queryset it was
called on is inspected. The counts the request produces are compared with
the separate requests' in ``test_product_search_sidebar_facets.py``.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from meili.querysets import FacetGroup, IndexQuerySet, StatsBaseline
from product.factories.category import ProductCategoryFactory
from search.models import SearchQuery

EMPTY = {
    "results": [],
    "estimated_total_hits": 0,
    "offset": 0,
    "limit": 20,
    "processing_time_ms": 1,
}


@pytest.fixture
def searched():
    """The querysets ``search`` was called on, in order."""
    calls: list[IndexQuerySet] = []

    def record(queryset, q=""):
        calls.append(queryset)
        return dict(EMPTY)

    with patch.object(IndexQuerySet, "search", autospec=True) as search:
        search.side_effect = record
        yield calls


def _get(**params):
    return APIClient().get(reverse("search-product"), params)


@pytest.mark.django_db
class TestSidebarFacetsRequest:
    def test_category_and_brand_become_whole_field_groups(self, searched):
        category = ProductCategoryFactory(parent=None)

        response = _get(
            categories=str(category.id),
            brands="4,5",
            facets="category,brand",
            sidebar_facets="true",
        )

        assert response.status_code == status.HTTP_200_OK
        (queryset,) = searched
        assert queryset._state.facet_groups == [
            FacetGroup(
                facet="category",
                clause=f"category IN [{category.id}]",
                selected=frozenset({str(category.id)}),
                members=None,
            ),
            FacetGroup(
                facet="brand",
                clause="brand IN [4, 5]",
                selected=frozenset({"4", "5"}),
                members=None,
            ),
        ]

    def test_a_facet_that_is_not_requested_stays_a_plain_filter(self, searched):
        _get(brands="4", facets="category", sidebar_facets="true")

        (queryset,) = searched
        assert queryset._state.facet_groups == []
        assert "brand IN [4]" in queryset.filters

    def test_without_the_flag_nothing_is_a_group(self, searched):
        category = ProductCategoryFactory(parent=None)

        _get(
            categories=str(category.id),
            brands="4",
            facets="category,brand,effective_price",
        )

        (queryset,) = searched
        assert queryset._state.facet_groups == []
        assert queryset._state.stats_baselines == []
        assert queryset.facets == ["category", "brand", "effective_price"]

    def test_the_ranges_leave_the_search_and_get_baselines(self, searched):
        scope = ProductCategoryFactory(parent=None)
        child = ProductCategoryFactory(parent=scope)

        _get(
            query="shoe",
            brands="4",
            price_min="5",
            language_code="en",
            facets="brand,effective_price,likes_count,view_count",
            sidebar_facets="true",
            scope_category=str(scope.id),
        )

        (queryset,) = searched
        assert queryset.facets == ["brand"]
        price, popularity = queryset._state.stats_baselines
        assert price == StatsBaseline(
            facets=("effective_price",),
            filters=(
                "active = True",
                "is_deleted = False",
                "language_code = 'en'",
                f"category IN [{scope.id}, {child.id}]",
            ),
            locales=("en",),
        )
        assert popularity == StatsBaseline(
            facets=("likes_count", "view_count"),
            filters=(
                "active = True",
                "is_deleted = False",
                "language_code = 'en'",
            ),
            locales=("en",),
        )

    def test_the_price_range_is_store_wide_without_a_scope(self, searched):
        _get(facets="effective_price", sidebar_facets="true")

        (queryset,) = searched
        (price,) = queryset._state.stats_baselines
        assert price.filters == ("active = True", "is_deleted = False")

    def test_a_range_that_is_not_requested_gets_no_baseline(self, searched):
        _get(
            facets="view_count",
            sidebar_facets="true",
            scope_category="1",
        )

        (queryset,) = searched
        (popularity,) = queryset._state.stats_baselines
        assert popularity.facets == ("view_count",)

    @pytest.mark.parametrize(
        "params",
        [{"sidebar_facets": "ture"}, {"scope_category": "x"}],
    )
    def test_garbage_is_a_400(self, params, searched):
        assert _get(**params).status_code == status.HTTP_400_BAD_REQUEST
        assert searched == []


@pytest.mark.django_db
class TestCountOnlyRequestsAreNotSearches:
    @pytest.mark.parametrize(
        ("params", "recorded"),
        [
            ({"query": "shoe"}, True),
            ({"query": "shoe", "limit": 1}, True),
            ({"query": "shoe", "facets": "category"}, True),
            ({"query": "shoe", "facets": "category", "limit": 20}, True),
            ({"query": "shoe", "facets": "category", "limit": 2}, True),
            ({"query": "shoe", "facets": "not-a-facet", "limit": 1}, True),
            ({"query": "shoe", "facets": "category", "limit": 1}, False),
            ({"query": "shoe", "facets": "category", "limit": 0}, False),
            ({"facets": "effective_price", "limit": 1}, False),
        ],
    )
    def test_a_facet_read_is_not_recorded(self, searched, params, recorded):
        before = SearchQuery.objects.count()

        response = _get(**params)

        assert response.status_code == status.HTTP_200_OK
        assert SearchQuery.objects.count() == before + (1 if recorded else 0)
