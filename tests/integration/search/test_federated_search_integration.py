"""Integration tests for the federated search endpoint and analytics.

The live tests index their own products and blog posts with
``LiveSearchIndex`` and search for a marker only those documents
contain, so they can assert exact hits, order and federation metadata
while every worker shares one engine (see
``tests/utils/meilisearch.py``).
"""

import os
import time
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from blog.factories.post import BlogPostFactory
from blog.models.post import BlogPostTranslation
from product.factories.product import ProductFactory
from product.models.product import ProductTranslation
from search.models import SearchQuery
from tests.conftest import requires_meilisearch
from tests.utils.meilisearch import (
    LiveSearchIndex,
    unique_greek_word,
    unique_marker,
)

User = get_user_model()

FEDERATED_URL = reverse("search-federated")


@pytest.fixture
def live_index(db):
    with LiveSearchIndex() as index:
        yield index


def make_product(names, *, active=True, **fields):
    return ProductFactory(
        price=Decimal("10.00"),
        discount_percent=0,
        vat=None,
        active=active,
        stock=5,
        translations=[
            ProductTranslation(language_code=code, name=name)
            for code, name in names.items()
        ],
        **fields,
    )


def make_post(titles, *, is_published=True):
    return BlogPostFactory(
        is_published=is_published,
        translations=[
            BlogPostTranslation(
                language_code=code, title=title, subtitle="", body=""
            )
            for code, title in titles.items()
        ],
    )


def translation(obj, language_code):
    return obj.translations.get(language_code=language_code)


def federated(**params):
    response = Client().get(FEDERATED_URL, params)
    assert response.status_code == 200, response.data
    return response.data


def hits(data):
    """``(content_type, id)`` per result, in response order."""
    return [(r["content_type"], r["id"]) for r in data["results"]]


@requires_meilisearch
@pytest.mark.django_db
class TestFederatedSearch:
    def test_merges_products_and_posts_weighting_posts_lower(self, live_index):
        marker = unique_marker()
        product = translation(make_product({"en": marker}), "en")
        post = translation(make_post({"en": marker}), "en")
        live_index.add(product, post)

        data = federated(query=marker)

        assert hits(data) == [("product", product.id), ("blog_post", post.id)]
        product_hit, post_hit = data["results"]
        assert product_hit["federation"]["indexUid"] == (
            ProductTranslation.get_meili_index_name()
        )
        assert post_hit["federation"]["indexUid"] == (
            BlogPostTranslation.get_meili_index_name()
        )
        assert product_hit["federation"]["weightedRankingScore"] == (
            pytest.approx(product_hit["ranking_score"])
        )
        assert post_hit["federation"]["weightedRankingScore"] == (
            pytest.approx(0.7 * post_hit["ranking_score"])
        )
        assert data["estimated_total_hits"] == 2

    def test_every_result_highlights_the_matched_term(self, live_index):
        """As the single-index endpoints do: the storefront shows the
        matched term marked in each result's ``formatted`` fields."""
        marker = unique_marker()
        product = translation(make_product({"en": f"{marker} lamp"}), "en")
        post = translation(make_post({"en": f"{marker} guide"}), "en")
        live_index.add(product, post)

        product_hit, post_hit = federated(query=marker)["results"]

        assert product_hit["formatted"]["name"] == f"<mark>{marker}</mark> lamp"
        assert post_hit["formatted"]["title"] == f"<mark>{marker}</mark> guide"

    def test_hides_inactive_deleted_products_and_unpublished_posts(
        self, live_index
    ):
        marker = unique_marker()
        product = translation(make_product({"en": f"{marker} on"}), "en")
        post = translation(make_post({"en": f"{marker} on"}), "en")
        hidden = [
            translation(
                make_product({"en": f"{marker} a"}, active=False), "en"
            ),
            translation(
                make_product({"en": f"{marker} b"}, is_deleted=True), "en"
            ),
            translation(
                make_post({"en": f"{marker} c"}, is_published=False), "en"
            ),
        ]
        live_index.add(product, post, *hidden)

        data = federated(query=marker)

        assert hits(data) == [("product", product.id), ("blog_post", post.id)]

    def test_language_code_filters_both_indexes(self, live_index):
        marker = unique_marker()
        product = make_product({"en": f"{marker} lamp", "el": f"{marker} φως"})
        post = make_post({"en": f"{marker} lamp", "el": f"{marker} φως"})
        live_index.add(*product.translations.all(), *post.translations.all())

        data = federated(query=marker, language_code="el")

        assert sorted(hits(data)) == [
            ("blog_post", translation(post, "el").id),
            ("product", translation(product, "el").id),
        ]

    def test_greeklish_query_matches_greek_products_and_posts(self, live_index):
        greek, latin = unique_greek_word()
        product = translation(make_product({"el": f"Λάμπα {greek}"}), "el")
        post = translation(make_post({"el": f"Οδηγός {greek}"}), "el")
        live_index.add(product, post)

        data = federated(query=latin)

        assert sorted(hits(data)) == [
            ("blog_post", post.id),
            ("product", product.id),
        ]

    def test_limit_and_offset_page_through_merged_results(self, live_index):
        marker = unique_marker()
        documents = [
            translation(make_product({"en": f"{marker} p{n}"}), "en")
            for n in range(2)
        ] + [
            translation(make_post({"en": f"{marker} b{n}"}), "en")
            for n in range(2)
        ]
        live_index.add(*documents)

        first = federated(query=marker, limit=3, offset=0)
        second = federated(query=marker, limit=3, offset=3)

        assert len(first["results"]) == 3
        assert len(second["results"]) == 1
        assert sorted(hits(first) + hits(second)) == sorted(
            (
                "product" if isinstance(d, ProductTranslation) else "blog_post",
                d.id,
            )
            for d in documents
        )
        assert first["estimated_total_hits"] == 4

    def test_special_characters_in_query_still_match(self, live_index):
        marker = unique_marker()
        product = translation(make_product({"en": f"{marker} phone"}), "en")
        live_index.add(product)

        for query in (
            f"{marker}!@#$%",
            f'"{marker}"',
            f"{marker}'s",
            f"{marker} & co",
        ):
            data = federated(query=query)
            assert hits(data) == [("product", product.id)], query

    def test_records_the_search_with_its_result_counts(self, live_index):
        marker = unique_marker()
        product = translation(make_product({"en": marker}), "en")
        post = translation(make_post({"en": marker}), "en")
        live_index.add(product, post)

        data = federated(query=marker, language_code="en")

        record = SearchQuery.objects.get(query=marker)
        assert record.uuid == data["query_id"]
        assert record.content_type == "federated"
        assert record.language_code == "en"
        assert record.results_count == 2
        assert record.estimated_total_hits == 2
        assert record.processing_time_ms is not None

    def test_limit_above_the_maximum_is_capped(self):
        data = federated(query=unique_marker(), limit=10_000)

        assert data["limit"] == 100
        assert data["results"] == []

    def test_performance(self, live_index):
        """The latency budget is only asserted when this process has the
        engine to itself: under ``-n auto`` every worker queries the
        same local Meilisearch at once, so the number measured here is
        contention, not the code path (a run once measured 30.09 s)."""
        marker = unique_marker()
        product = translation(make_product({"en": marker}), "en")
        live_index.add(product)

        start = time.monotonic()
        data = federated(query=marker)
        duration = time.monotonic() - start

        assert hits(data) == [("product", product.id)]
        workers = int(os.environ.get("PYTEST_XDIST_WORKER_COUNT", "1") or 1)
        if workers > 1:
            pytest.skip(
                f"latency budget not meaningful across {workers} workers "
                f"sharing one Meilisearch (measured {duration:.2f}s)"
            )
        assert duration < 2.0


@pytest.mark.django_db
class TestFederatedSearchValidation:
    @pytest.mark.parametrize(
        "params",
        [
            {"language_code": "en"},
            {"query": "", "language_code": "en"},
            {"query": "lamp", "limit": "many"},
            {"query": "lamp", "language_code": "xx"},
        ],
        ids=["missing-query", "empty-query", "bad-limit", "bad-language"],
    )
    def test_rejects_invalid_parameters(self, params):
        response = Client().get(FEDERATED_URL, params)

        assert response.status_code == 400


@pytest.mark.django_db
class TestSearchAnalyticsIntegration:
    """Integration tests for search analytics endpoint."""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Set up test client and data."""
        self.client = Client()
        self.analytics_url = "/api/v1/search/analytics"
        # Analytics endpoint requires admin authentication
        self.admin_user = User.objects.create_superuser(
            username="analytics_admin",
            email="admin@example.com",
            password="testpass123",
        )
        self.client.force_login(self.admin_user)

    def test_analytics_endpoint_returns_aggregated_metrics(self):
        """
        Test that analytics endpoint returns all required metrics.
        """
        response = self.client.get(
            self.analytics_url,
            {"start_date": "2024-01-01", "end_date": "2024-12-31"},
        )

        assert response.status_code == 200
        data = response.json()

        # Verify response structure (camelCase due to DRF middleware)
        assert "dateRange" in data
        assert "topQueries" in data
        assert "zeroResultQueries" in data
        assert "searchVolume" in data
        assert "performance" in data
        assert "clickThroughRate" in data

    def test_analytics_filters_by_date_range(self):
        """Only searches inside [start_date, end_date] are counted."""
        from datetime import timedelta

        from django.utils import timezone

        # ``timestamp`` is ``auto_now_add``: a value passed to create()
        # is discarded, so the rows are backdated with update().
        for query, days_ago in (("old query", 365), ("recent query", 1)):
            row = SearchQuery.objects.create(
                query=query,
                language_code="en",
                content_type="product",
                results_count=10,
                estimated_total_hits=10,
            )
            SearchQuery.objects.filter(pk=row.pk).update(
                timestamp=timezone.now() - timedelta(days=days_ago)
            )

        response = self.client.get(
            self.analytics_url,
            {
                "start_date": (timezone.now() - timedelta(days=7)).strftime(
                    "%Y-%m-%d"
                ),
                "end_date": timezone.now().strftime("%Y-%m-%d"),
            },
        )

        assert response.status_code == 200
        top_queries = [q["query"] for q in response.json()["topQueries"]]
        assert top_queries == ["recent query"]

    @pytest.mark.parametrize(
        ("stamped_at", "counted"),
        [
            ("2026-03-09 23:59:59", False),
            ("2026-03-10 00:00:00", True),
            ("2026-03-12 23:59:59", True),
            ("2026-03-13 00:00:00", False),
        ],
    )
    def test_analytics_date_range_covers_both_whole_days(
        self, stamped_at, counted
    ):
        """``end_date`` is a day, not the midnight that starts it: a
        search made during the end day counts. Days are the store's
        local days, not UTC ones."""
        from datetime import datetime

        from django.utils import timezone

        row = SearchQuery.objects.create(
            query="boundary",
            language_code="en",
            content_type="product",
            results_count=1,
            estimated_total_hits=1,
        )
        SearchQuery.objects.filter(pk=row.pk).update(
            timestamp=timezone.make_aware(datetime.fromisoformat(stamped_at))
        )

        response = self.client.get(
            self.analytics_url,
            {"start_date": "2026-03-10", "end_date": "2026-03-12"},
        )

        assert response.status_code == 200, response.json()
        top_queries = [q["query"] for q in response.json()["topQueries"]]
        assert top_queries == (["boundary"] if counted else [])

    @pytest.mark.parametrize(
        "params",
        [
            {"start_date": "2026-03-10T12:00:00"},
            {"end_date": "10/03/2026"},
            {"start_date": "2026-03-12", "end_date": "2026-03-10"},
        ],
    )
    def test_analytics_rejects_a_malformed_date_range(self, params):
        response = self.client.get(self.analytics_url, params)

        assert response.status_code == 400, response.json()

    def test_analytics_filters_by_content_type(self):
        """
        Test that analytics endpoint filters by content_type.
        """
        # Create test search queries with different content types
        SearchQuery.objects.create(
            query="product query",
            language_code="en",
            content_type="product",
            results_count=10,
            estimated_total_hits=10,
        )

        SearchQuery.objects.create(
            query="blog query",
            language_code="en",
            content_type="blog_post",
            results_count=5,
            estimated_total_hits=5,
        )

        # Query product searches only
        response = self.client.get(
            self.analytics_url, {"content_type": "product"}
        )

        assert response.status_code == 200
        data = response.json()

        assert data["searchVolume"]["byContentType"] == {"product": 1}
        assert [q["query"] for q in data["topQueries"]] == ["product query"]

    def test_analytics_handles_no_data(self):
        """
        Test that analytics endpoint handles case with no search data.
        """
        # Clear all search queries
        SearchQuery.objects.all().delete()

        response = self.client.get(self.analytics_url)

        assert response.status_code == 200
        data = response.json()

        # Verify empty metrics (camelCase due to DRF middleware)
        assert data["topQueries"] == []
        assert data["zeroResultQueries"] == []
        assert data["searchVolume"]["total"] == 0
        assert data["clickThroughRate"] == 0
