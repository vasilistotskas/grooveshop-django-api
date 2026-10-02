"""
Integration tests for search views with property validation.

Tests the complete search flow including analytics tracking, federated search,
and ensures that all properties hold across various search scenarios.
"""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from rest_framework import status

from blog.factories.post import BlogPostFactory
from blog.models.post import BlogPostTranslation
from product.factories.product import ProductFactory
from product.models.product import ProductTranslation
from search.models import SearchClick, SearchQuery
from tests.conftest import requires_meilisearch
from tests.utils.meilisearch import LiveSearchIndex, unique_marker

User = get_user_model()


@pytest.fixture
def api_client():
    """Provide Django test client for API requests."""
    return Client()


@pytest.fixture
def admin_client(db):
    """Provide Django test client authenticated as admin."""
    client = Client()
    admin_user = User.objects.create_superuser(
        username="admin_user",
        email="admin@example.com",
        password="testpass123",
    )
    client.force_login(admin_user)
    return client


@pytest.mark.django_db
class TestAnalyticsMetricsCompleteness:
    """
    For any analytics query, the response should include top_queries,
    zero_result_queries, search_volume, avg_results_count, and
    click_through_rate metrics.
    """

    @pytest.mark.parametrize(
        "start_date,end_date,content_type",
        [
            (None, None, None),  # No filters
            ("2024-01-01", None, None),  # Start date only
            (None, "2024-12-31", None),  # End date only
            ("2024-01-01", "2024-12-31", None),  # Date range
            (None, None, "product"),  # Content type filter
            (None, None, "blog_post"),
            (None, None, "federated"),
            ("2024-01-01", "2024-12-31", "product"),  # All filters
            ("2024-06-01", "2024-06-30", "blog_post"),
        ],
    )
    def test_analytics_response_includes_all_required_metrics(
        self,
        admin_client,
        start_date,
        end_date,
        content_type,
    ):
        """
        Test that analytics response includes all required metrics.

        This property test validates that regardless of the filters applied,
        the analytics endpoint always returns a complete set of metrics.
        """
        # Create some test data
        for i in range(5):
            search_query = SearchQuery.objects.create(
                query=f"query {i}",
                language_code="en",
                content_type=content_type or "product",
                results_count=i * 2,
                estimated_total_hits=i * 10,
                processing_time_ms=50 + i * 10,
            )

            # Create some clicks
            if i % 2 == 0:
                SearchClick.objects.create(
                    search_query=search_query,
                    result_id=str(i),
                    result_type="product",
                    position=0,
                )

        # Build query parameters
        params = {}
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        if content_type:
            params["content_type"] = content_type

        # Make request to analytics endpoint
        response = admin_client.get("/api/v1/search/analytics", params)

        # Verify response is successful
        assert response.status_code == status.HTTP_200_OK, (
            f"Analytics endpoint should return 200, got {response.status_code}"
        )

        # Parse response data
        data = response.json()

        # Verify all required top-level fields are present
        required_fields = [
            "dateRange",
            "topQueries",
            "zeroResultQueries",
            "searchVolume",
            "performance",
            "clickThroughRate",
        ]

        for field in required_fields:
            assert field in data, (
                f"Analytics response should include '{field}' field"
            )

        # Verify dateRange structure
        assert "start" in data["dateRange"], (
            "dateRange should include 'start' field"
        )
        assert "end" in data["dateRange"], (
            "dateRange should include 'end' field"
        )

        # Verify topQueries structure
        assert isinstance(data["topQueries"], list), (
            "topQueries should be a list"
        )
        if len(data["topQueries"]) > 0:
            top_query = data["topQueries"][0]
            assert "query" in top_query, "topQuery should include 'query' field"
            assert "count" in top_query, "topQuery should include 'count' field"
            assert "avgResults" in top_query, (
                "topQuery should include 'avgResults' field"
            )
            assert "clickThroughRate" in top_query, (
                "topQuery should include 'clickThroughRate' field"
            )

        # Verify zeroResultQueries structure
        assert isinstance(data["zeroResultQueries"], list), (
            "zeroResultQueries should be a list"
        )

        # Verify searchVolume structure
        assert "total" in data["searchVolume"], (
            "searchVolume should include 'total' field"
        )
        assert "byContentType" in data["searchVolume"], (
            "searchVolume should include 'byContentType' field"
        )
        assert "byLanguage" in data["searchVolume"], (
            "searchVolume should include 'byLanguage' field"
        )

        # Verify performance structure
        assert "avgProcessingTimeMs" in data["performance"], (
            "performance should include 'avgProcessingTimeMs' field"
        )
        assert "avgResultsCount" in data["performance"], (
            "performance should include 'avgResultsCount' field"
        )

        # Verify clickThroughRate is a number
        assert isinstance(data["clickThroughRate"], (int, float)), (
            "clickThroughRate should be a number"
        )

    def test_analytics_with_zero_result_queries(self, admin_client):
        """Test that zero-result queries are properly included in analytics."""
        # Create queries with zero results
        for i in range(3):
            SearchQuery.objects.create(
                query=f"zero result query {i}",
                language_code="en",
                content_type="product",
                results_count=0,  # Zero results
                estimated_total_hits=0,
            )

        # Create queries with results
        for i in range(2):
            SearchQuery.objects.create(
                query=f"normal query {i}",
                language_code="en",
                content_type="product",
                results_count=10,
                estimated_total_hits=100,
            )

        # Make request
        response = admin_client.get("/api/v1/search/analytics")

        # Verify response is successful
        assert response.status_code == status.HTTP_200_OK, (
            f"Analytics endpoint should return 200, got {response.status_code}"
        )

        data = response.json()

        # Verify zeroResultQueries are included
        assert "zeroResultQueries" in data, (
            f"Response should include 'zeroResultQueries' field. Got keys: {list(data.keys())}"
        )
        assert len(data["zeroResultQueries"]) >= 3, (
            "Zero-result queries should be included in analytics"
        )

    def test_analytics_calculates_click_through_rate(self, admin_client):
        """Test that click-through rate is calculated correctly."""
        # Create queries with clicks
        for i in range(10):
            search_query = SearchQuery.objects.create(
                query=f"query {i}",
                language_code="en",
                content_type="product",
                results_count=5,
                estimated_total_hits=50,
            )

            # Add clicks to half of the queries
            if i < 5:
                SearchClick.objects.create(
                    search_query=search_query,
                    result_id=str(i),
                    result_type="product",
                    position=0,
                )

        # Make request
        response = admin_client.get("/api/v1/search/analytics")

        # Verify response is successful
        assert response.status_code == status.HTTP_200_OK, (
            f"Analytics endpoint should return 200, got {response.status_code}"
        )

        data = response.json()

        # Verify CTR is calculated (5 clicks / 10 searches = 0.5)
        assert "clickThroughRate" in data, (
            f"Response should include 'clickThroughRate' field. Got keys: {list(data.keys())}"
        )
        assert data["clickThroughRate"] > 0, (
            "Click-through rate should be greater than 0"
        )
        assert data["clickThroughRate"] <= 1.0, (
            "Click-through rate should not exceed 1.0"
        )

    def test_top_query_counts_immune_to_click_join_fanout(self, admin_client):
        """Multiple clicks on one SearchQuery row must not inflate that
        query's search count or skew its average (the multi-aggregate
        JOIN fan-out pitfall)."""
        from search.models import SearchClick, SearchQuery

        rows = [
            SearchQuery.objects.create(
                query="bataria kinitou",
                language_code="el",
                content_type="federated",
                results_count=results,
                estimated_total_hits=results,
            )
            for results in (4, 8)
        ]
        for _ in range(3):
            SearchClick.objects.create(
                search_query=rows[0],
                result_id="37",
                result_type="blog_post",
                position=0,
            )

        response = admin_client.get("/api/v1/search/analytics")
        assert response.status_code == 200
        top = {row["query"]: row for row in response.json()["topQueries"]}
        row = top["bataria kinitou"]
        assert row["count"] == 2  # two searches, regardless of clicks
        assert row["avgResults"] == 6.0  # mean of 4 and 8, unweighted
        assert row["clickThroughRate"] == 1.5  # 3 clicks / 2 searches

    def test_zero_result_list_immune_to_lane_split_rows(self, admin_client):
        """One user search logs separate product/blog/federated rows.
        A query whose blog lane found results must not appear in the
        zero-result list because its product lane logged 0."""
        for content_type, results in (("product", 0), ("blog_post", 11)):
            SearchQuery.objects.create(
                query="windows",
                language_code="el",
                content_type=content_type,
                results_count=results,
                estimated_total_hits=results,
            )
        SearchQuery.objects.create(
            query="asirmati skoupa",
            language_code="el",
            content_type="product",
            results_count=0,
            estimated_total_hits=0,
        )

        response = admin_client.get("/api/v1/search/analytics")
        assert response.status_code == 200
        zero = [q["query"] for q in response.json()["zeroResultQueries"]]
        assert "windows" not in zero
        assert "asirmati skoupa" in zero


@requires_meilisearch
@pytest.mark.django_db
class TestAnalyticsLoggingFailuresDontBreakSearch:
    """A search whose analytics row cannot be written still answers.

    ``SearchAnalyticsMiddleware`` persists the ``SearchQuery`` row after
    the view has answered; a failure there must be swallowed, never turn
    into an error or an emptied result list.
    """

    @pytest.fixture
    def indexed(self, db):
        """One product and one post whose text is a unique marker."""
        marker = unique_marker()
        with LiveSearchIndex() as index:
            product = ProductFactory(
                active=True,
                translations=[
                    ProductTranslation(language_code="en", name=marker)
                ],
            ).translations.get()
            post = BlogPostFactory(
                is_published=True,
                translations=[
                    BlogPostTranslation(
                        language_code="en", title=marker, subtitle="", body=""
                    )
                ],
            ).translations.get()
            index.add(product, post)
            yield marker, {"product": product.id, "post": post.id}

    @pytest.mark.parametrize(
        "endpoint,expected",
        [
            ("/api/v1/search/product", ["product"]),
            ("/api/v1/search/blog/post", ["post"]),
            ("/api/v1/search/federated", ["product", "post"]),
        ],
    )
    def test_search_succeeds_when_analytics_logging_fails(
        self, api_client, indexed, endpoint, expected
    ):
        marker, ids = indexed

        with patch.object(
            SearchQuery.objects,
            "create",
            side_effect=Exception("Database error"),
        ):
            response = api_client.get(
                endpoint, {"query": marker, "language_code": "en"}
            )

        assert response.status_code == status.HTTP_200_OK
        assert [r["id"] for r in response.data["results"]] == [
            ids[kind] for kind in expected
        ]
        assert not SearchQuery.objects.exists()

    def test_search_succeeds_when_no_row_can_be_saved(
        self, api_client, indexed
    ):
        marker, ids = indexed

        with patch(
            "django.db.models.Model.save", side_effect=Exception("DB error")
        ):
            response = api_client.get(
                "/api/v1/search/product",
                {"query": marker, "language_code": "en"},
            )

        assert response.status_code == status.HTTP_200_OK
        assert [r["id"] for r in response.data["results"]] == [ids["product"]]


@pytest.mark.django_db
class TestFederatedSearchSendsQueryVerbatim:
    """The engine is mocked: this pins the request the view builds.

    What the engine then returns for a real query is covered by the live
    tests in ``test_federated_search_integration.py``.
    """

    def test_federated_search_sends_query_unmodified(self, api_client):
        """
        Greek and Greeklish queries must reach Meilisearch verbatim —
        Greeklish matching happens against the indexed ``*_greeklish``
        shadow fields, not through query rewriting.

        Validates: Requirements 1.6
        """
        with patch(
            "meili._client.client.search_client.multi_search"
        ) as mock_multi_search:
            mock_multi_search.return_value = {
                "hits": [],
                "estimatedTotalHits": 0,
                "processingTimeMs": 30,
            }

            api_client.get(
                "/api/v1/search/federated",
                {
                    "query": "anavathmisi se windows",
                    "language_code": "el",
                    "limit": 20,
                },
            )

            assert mock_multi_search.called, "multi_search should be called"
            # The zero-result relaxed fallback may issue a second call
            # with the leading word dropped — the FIRST call must carry
            # the raw query verbatim.
            first_call = mock_multi_search.call_args_list[0]
            queries = first_call.kwargs.get("queries") or (
                first_call[1].get("queries") if len(first_call) > 1 else None
            )

            assert [query["q"] for query in queries] == [
                "anavathmisi se windows",
                "anavathmisi se windows",
            ], "The raw query must reach both indexes unmodified"


@pytest.mark.django_db
class TestProductSearchCategorySubtree:
    """``categories`` names a category and everything under it.

    The index stores each product's own category, so a category whose
    products all sit in subcategories listed 0 of them under a header
    counting them recursively. The filter now names the subtree.
    """

    def _category_filter(self, api_client, categories: str) -> str:
        index = Mock()
        index.search.return_value = {
            "hits": [],
            "estimatedTotalHits": 0,
            "processingTimeMs": 1,
        }
        with patch("meili._client.client.get_search_index", return_value=index):
            response = api_client.get(
                "/api/v1/search/product",
                {"query": "", "language_code": "el", "categories": categories},
            )
        assert response.status_code == status.HTTP_200_OK
        filters = index.search.call_args.args[1]["filter"]
        return next(f for f in filters if f.startswith("category IN"))

    def test_a_parent_category_matches_its_whole_subtree(self, api_client):
        from product.factories.category import ProductCategoryFactory

        root = ProductCategoryFactory()
        child = ProductCategoryFactory(parent=root)
        grandchild = ProductCategoryFactory(parent=child)
        ProductCategoryFactory()  # an unrelated root stays out

        assert self._category_filter(api_client, str(root.id)) == (
            f"category IN {sorted([root.id, child.id, grandchild.id])}"
        )

    def test_a_leaf_category_matches_itself(self, api_client):
        from product.factories.category import ProductCategoryFactory

        leaf = ProductCategoryFactory(parent=ProductCategoryFactory())

        assert self._category_filter(api_client, str(leaf.id)) == (
            f"category IN {[leaf.id]}"
        )

    def test_an_unknown_category_still_filters_to_nothing(self, api_client):
        assert self._category_filter(api_client, "999999") == (
            "category IN [999999]"
        )


@pytest.mark.django_db
class TestProductSearchRelaxedRetry:
    """``/search/product`` retries a zero-hit multi-word query once with
    its leading word dropped — the one shape Meilisearch's ``last``
    matching strategy can never relax — and says so when it worked.

    The engine is mocked; the hit ids are real rows, so the response is
    hydrated and serialized exactly as in production.
    """

    QUERY = "anaba8mish se windows"

    def _search(self, api_client, *engine_answers):
        index = Mock()
        index.search.side_effect = list(engine_answers)
        with patch("meili._client.client.get_search_index", return_value=index):
            response = api_client.get(
                "/api/v1/search/product",
                {"query": self.QUERY, "language_code": "el"},
            )
        assert response.status_code == status.HTTP_200_OK
        return response, [call.args[0] for call in index.search.call_args_list]

    def test_retry_hit_is_served_and_disclosed(self, api_client):
        translation = ProductFactory(
            active=True, num_images=0, num_reviews=0
        ).translations.get(language_code="el")

        response, queries = self._search(
            api_client,
            {"hits": [], "estimatedTotalHits": 0, "processingTimeMs": 3},
            {
                "hits": [{"id": translation.id}],
                "estimatedTotalHits": 1,
                "processingTimeMs": 4,
            },
        )

        assert queries == [self.QUERY, "se windows"]
        assert response.data["relaxed_query"] == "se windows"
        assert [r["id"] for r in response.data["results"]] == [translation.id]
        # Both engine calls are the request's search time.
        assert SearchQuery.objects.get().processing_time_ms == 7

    def test_empty_retry_is_not_claimed(self, api_client):
        response, queries = self._search(
            api_client,
            {"hits": [], "estimatedTotalHits": 0, "processingTimeMs": 3},
            {"hits": [], "estimatedTotalHits": 0, "processingTimeMs": 4},
        )

        assert queries == [self.QUERY, "se windows"]
        assert response.data["relaxed_query"] is None
        assert response.data["results"] == []


@pytest.mark.django_db
class TestManagementCommandsExecution:
    """
    End-to-end tests for management commands execution.

    Tests that management commands can be executed successfully and
    produce expected results.

    Validates: Requirements 9.1, 9.2, 9.3, 9.4, 9.8
    """

    def test_meilisearch_update_index_settings_command(self):
        """The command must update ONLY the provided settings via their
        dedicated endpoints â€” never the full-payload update_settings, which
        would wipe filterable/sortable/searchable/synonyms (G0172).
        """
        from io import StringIO

        from django.core.management import call_command

        with patch("meili._client.client.client.index") as mock_index:
            mock_index_obj = Mock()
            mock_index.return_value = mock_index_obj

            out = StringIO()
            call_command(
                "meilisearch_update_index_settings",
                "--index",
                "ProductTranslation",
                "--max-total-hits",
                "50000",
                "--search-cutoff-ms",
                "1500",
                stdout=out,
            )

            mock_index_obj.update_pagination_settings.assert_called_once_with(
                {"maxTotalHits": 50000}
            )
            mock_index_obj.update_search_cutoff_ms.assert_called_once_with(1500)
            # A facet limit was NOT passed, so its endpoint must not fire.
            mock_index_obj.update_faceting_settings.assert_not_called()
            # The full-payload wipe path must never be used.
            mock_index_obj.update_settings.assert_not_called()

    def test_meilisearch_update_ranking_command(self):
        """The command must update ONLY the ranking rules via the dedicated
        endpoint â€” never the full-payload update_settings (G0172)."""
        from io import StringIO

        from django.core.management import call_command

        with patch("meili._client.client.client.index") as mock_index:
            mock_index_obj = Mock()
            mock_index.return_value = mock_index_obj

            out = StringIO()
            call_command(
                "meilisearch_update_ranking",
                "--index",
                "ProductTranslation",
                "--rules",
                "words,typo,proximity,attribute,sort,exactness",
                stdout=out,
            )

            mock_index_obj.update_ranking_rules.assert_called_once_with(
                ["words", "typo", "proximity", "attribute", "sort", "exactness"]
            )
            mock_index_obj.update_settings.assert_not_called()
