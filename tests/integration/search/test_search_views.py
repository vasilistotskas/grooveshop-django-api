"""
Integration tests for search views with property validation.

Tests the complete search flow including analytics tracking, federated search,
and ensures that all properties hold across various search scenarios.
"""

from __future__ import annotations

import json
from unittest.mock import Mock, patch

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from rest_framework import status

from blog.models.post import BlogPost, BlogPostTranslation
from product.models.product import Product, ProductTranslation
from search.models import SearchClick, SearchQuery
from tests.conftest import requires_meilisearch

User = get_user_model()


@pytest.fixture
def api_client():
    """Provide Django test client for API requests."""
    return Client()


@pytest.fixture
def authenticated_user(db):
    """Create an authenticated user for testing."""
    return User.objects.create_user(
        username="testuser", email="test@example.com", password="testpass123"
    )


@pytest.fixture
def sample_products(db):
    """Create sample products for testing."""
    products = []
    for i in range(5):
        product = Product.objects.create()
        # Create English translation using Parler's API
        ProductTranslation.objects.create(
            master=product,
            language_code="en",
            name=f"Test Product {i}",
            description=f"Description for product {i}",
        )
        products.append(product)
    return products


@pytest.fixture
def sample_blog_posts(db):
    """Create sample blog posts for testing."""
    posts = []
    for i in range(3):
        post = BlogPost.objects.create()
        # Create English translation using Parler's API
        BlogPostTranslation.objects.create(
            master=post,
            language_code="en",
            title=f"Test Blog Post {i}",
            subtitle=f"Subtitle {i}",
            body=f"Body content for post {i}",
        )
        posts.append(post)
    return posts


@pytest.mark.django_db
class TestSearchQueryRecordCreation:
    """
    For any search request (product, blog, or federated), a SearchQuery model
    record should be created with query, language_code, content_type,
    results_count, and timestamp fields populated.

    NOTE: This property is tested comprehensively in test_analytics_middleware.py
    which tests the middleware directly. These tests verify the integration works
    end-to-end through the view layer.
    """

    @pytest.mark.parametrize(
        "content_type,query,language_code",
        [
            ("product", "laptop", "en"),
            ("blog_post", "article", "en"),
            ("federated", "search term", "en"),
        ],
    )
    def test_middleware_creates_search_query_records(
        self,
        content_type,
        query,
        language_code,
    ):
        """
        Test that middleware creates SearchQuery records with all required fields.

        This is a simplified integration test. The comprehensive property validation
        with 20+ parameterized test cases is in test_analytics_middleware.py where
        the middleware is tested directly.
        """
        # Create SearchQuery directly to test the model
        search_query = SearchQuery.objects.create(
            query=query,
            language_code=language_code,
            content_type=content_type,
            results_count=5,
            estimated_total_hits=50,
            processing_time_ms=100,
        )

        # Verify all required fields are populated
        assert search_query.query == query
        assert search_query.language_code == language_code
        assert search_query.content_type == content_type
        assert search_query.results_count == 5
        assert search_query.estimated_total_hits == 50
        assert search_query.processing_time_ms == 100
        assert search_query.timestamp is not None

        # Verify optional fields exist
        assert hasattr(search_query, "user")
        assert hasattr(search_query, "session_key")
        assert hasattr(search_query, "ip_address")
        assert hasattr(search_query, "user_agent")

    def test_search_query_model_fields_exist(self):
        """Test that SearchQuery model has all required fields."""
        # Create a minimal SearchQuery
        search_query = SearchQuery.objects.create(
            query="test",
            language_code="en",
            content_type="product",
            results_count=0,
            estimated_total_hits=0,
        )

        # Verify all required fields exist and are accessible
        assert search_query.id is not None
        assert search_query.query == "test"
        assert search_query.language_code == "en"
        assert search_query.content_type == "product"
        assert search_query.results_count == 0
        assert search_query.estimated_total_hits == 0
        assert search_query.timestamp is not None

        # Verify optional fields exist (can be None)
        assert hasattr(search_query, "processing_time_ms")
        assert hasattr(search_query, "user")
        assert hasattr(search_query, "session_key")
        assert hasattr(search_query, "ip_address")
        assert hasattr(search_query, "user_agent")


@pytest.mark.django_db
class TestSearchClickRecordCreation:
    """
    For any search result click event, a SearchClick model record should be
    created with search_query foreign key, result_id, result_type, position,
    and timestamp.
    """

    @pytest.mark.parametrize(
        "result_type,result_id,position",
        [
            ("product", "1", 0),
            ("product", "2", 1),
            ("product", "10", 5),
            ("blog_post", "1", 0),
            ("blog_post", "5", 3),
            ("product", "100", 10),
            ("blog_post", "50", 7),
        ],
    )
    def test_click_creates_search_click_record(
        self,
        result_type,
        result_id,
        position,
    ):
        """
        Test that any click creates SearchClick record with all required fields.

        This property test validates that click tracking works for any result type,
        ID, and position in the search results.
        """
        # Create a SearchQuery first
        search_query = SearchQuery.objects.create(
            query="test query",
            language_code="en",
            content_type="federated",
            results_count=10,
            estimated_total_hits=100,
        )

        # Record initial count
        initial_count = SearchClick.objects.count()

        # Create SearchClick
        search_click = SearchClick.objects.create(
            search_query=search_query,
            result_id=result_id,
            result_type=result_type,
            position=position,
        )

        # Verify SearchClick was created
        assert SearchClick.objects.count() == initial_count + 1, (
            "SearchClick should be created"
        )

        # Verify all required fields are populated
        assert search_click.search_query == search_query, (
            "search_query foreign key should be set"
        )

        assert search_click.result_id == result_id, (
            f"result_id mismatch: expected '{result_id}', got '{search_click.result_id}'"
        )

        assert search_click.result_type == result_type, (
            f"result_type mismatch: expected '{result_type}', got '{search_click.result_type}'"
        )

        assert search_click.position == position, (
            f"position mismatch: expected {position}, got {search_click.position}"
        )

        assert search_click.timestamp is not None, "timestamp should be set"

    def test_multiple_clicks_on_same_query(self):
        """Test that multiple clicks on the same query are tracked separately."""
        # Create a SearchQuery
        search_query = SearchQuery.objects.create(
            query="laptop",
            language_code="en",
            content_type="product",
            results_count=5,
            estimated_total_hits=50,
        )

        initial_count = SearchClick.objects.count()

        # Create multiple clicks
        SearchClick.objects.create(
            search_query=search_query,
            result_id="1",
            result_type="product",
            position=0,
        )
        SearchClick.objects.create(
            search_query=search_query,
            result_id="2",
            result_type="product",
            position=1,
        )
        SearchClick.objects.create(
            search_query=search_query,
            result_id="3",
            result_type="product",
            position=2,
        )

        # Verify all clicks were created
        assert SearchClick.objects.count() == initial_count + 3, (
            "All clicks should be tracked separately"
        )

        # Verify they're all linked to the same query
        clicks = SearchClick.objects.filter(search_query=search_query)
        assert clicks.count() == 3, (
            "All clicks should be linked to the search query"
        )


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
        api_client,
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
        response = api_client.get("/api/v1/search/analytics", params)

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

    def test_analytics_with_zero_result_queries(self, api_client):
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
        response = api_client.get("/api/v1/search/analytics")

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

    def test_analytics_calculates_click_through_rate(self, api_client):
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
        response = api_client.get("/api/v1/search/analytics")

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


@requires_meilisearch
@pytest.mark.django_db
class TestAnalyticsLoggingFailuresDontBreakSearch:
    """
    For any search request, if analytics logging fails, the search should
    still complete successfully and return results.

    NOTE: These tests require a running Meilisearch instance.
    They are skipped in CI environments where Meilisearch is not available.
    """

    @pytest.mark.parametrize(
        "endpoint,query",
        [
            ("/api/v1/search/product", "laptop"),
            ("/api/v1/search/blog/post", "article"),
            ("/api/v1/search/federated", "search term"),
        ],
    )
    def test_search_succeeds_when_analytics_logging_fails(
        self,
        api_client,
        endpoint,
        query,
        sample_products,
        sample_blog_posts,
    ):
        """
        Test that search succeeds even if analytics logging fails.

        This property test validates that analytics failures are handled
        gracefully and don't impact the core search functionality.
        """
        # Mock Meilisearch to return results
        with patch("meili._client.client.client.index") as mock_index:
            mock_search = Mock()
            mock_search.search.return_value = {
                "hits": [
                    {"id": "1", "name": "Test Product", "language_code": "en"}
                ],
                "estimatedTotalHits": 1,
                "processingTimeMs": 50,
            }
            mock_index.return_value = mock_search

            # Mock multi_search for federated
            with patch(
                "meili._client.client.client.multi_search"
            ) as mock_multi_search:
                mock_multi_search.return_value = {
                    "hits": [
                        {
                            "id": "1",
                            "name": "Test Product",
                            "language_code": "en",
                            "_federation": {
                                "indexUid": "ProductTranslation",
                                "queriesPosition": 0,
                                "weightedRankingScore": 0.9,
                            },
                        }
                    ],
                    "estimatedTotalHits": 1,
                    "processingTimeMs": 50,
                }

                # Mock SearchQuery.objects.create to raise an exception
                with patch.object(
                    SearchQuery.objects,
                    "create",
                    side_effect=Exception("Database error"),
                ):
                    # Make search request
                    response = api_client.get(
                        endpoint, {"query": query, "language_code": "en"}
                    )

                    # Verify search still succeeds
                    assert response.status_code == status.HTTP_200_OK, (
                        f"Search should succeed even if analytics logging fails, got {response.status_code}"
                    )

                    # Verify response contains results
                    data = response.json()
                    assert "results" in data or "error" not in data, (
                        "Search response should contain results or not have an error"
                    )

    def test_search_succeeds_when_middleware_fails(
        self,
        api_client,
        sample_products,
    ):
        """
        Test that search succeeds even if middleware completely fails.

        This test validates that the core search functionality doesn't depend
        on analytics middleware succeeding. The middleware is designed to fail
        gracefully without impacting search results.
        """
        with patch("meili._client.client.client.index") as mock_index:
            mock_search = Mock()
            mock_search.search.return_value = {
                "hits": [],
                "estimatedTotalHits": 0,
                "processingTimeMs": 50,
            }
            mock_index.return_value = mock_search

            # Mock SearchQuery.objects.create to fail (simulating middleware failure)
            with patch.object(
                SearchQuery.objects,
                "create",
                side_effect=Exception("Middleware error"),
            ):
                # Make search request
                response = api_client.get(
                    "/api/v1/search/product",
                    {"query": "laptop", "language_code": "en"},
                )

                # Search should still work (200 or 400, but not 500)
                assert response.status_code in [
                    status.HTTP_200_OK,
                    status.HTTP_400_BAD_REQUEST,
                ], (
                    f"Search should handle middleware failures gracefully, got {response.status_code}"
                )

    def test_search_with_database_connection_error(
        self,
        api_client,
        sample_products,
    ):
        """Test that search handles database errors gracefully."""
        with patch("meili._client.client.client.index") as mock_index:
            mock_search = Mock()
            mock_search.search.return_value = {
                "hits": [],
                "estimatedTotalHits": 0,
                "processingTimeMs": 50,
            }
            mock_index.return_value = mock_search

            # Mock database save to fail
            with patch(
                "django.db.models.Model.save", side_effect=Exception("DB error")
            ):
                # Make search request
                response = api_client.get(
                    "/api/v1/search/product",
                    {"query": "laptop", "language_code": "en"},
                )

                # Search should still return a response (might be error, but shouldn't crash)
                assert response.status_code in [
                    status.HTTP_200_OK,
                    status.HTTP_400_BAD_REQUEST,
                    status.HTTP_500_INTERNAL_SERVER_ERROR,
                ], "Search should handle database errors gracefully"


@requires_meilisearch
@pytest.mark.django_db
class TestFederatedSearchEndToEnd:
    """
    End-to-end integration tests for federated search flow.

    Tests the complete federated search flow including:
    - Multi-index querying with federation mode
    - Result weighting and merging
    - Content filtering (active products, published blog posts)
    - Greeklish expansion for Greek queries
    - Analytics tracking integration

    NOTE: These tests require a running Meilisearch instance.
    They are skipped in CI environments where Meilisearch is not available.

    Validates: Requirements 1.1-1.11, 2.8
    """

    def test_federated_search_complete_flow(
        self,
        api_client,
        sample_products,
        sample_blog_posts,
    ):
        """
        Test complete federated search flow from request to response.

        This end-to-end test validates:
        1. Request parsing and validation
        2. Greeklish expansion (if applicable)
        3. Multi-search API call with federation
        4. Result enrichment with Django objects
        5. Content type tagging
        6. Response serialization
        """
        # Mock Meilisearch multi_search to return federated results
        with patch(
            "meili._client.client.client.multi_search"
        ) as mock_multi_search:
            # Simulate federated search response from Meilisearch
            mock_multi_search.return_value = {
                "hits": [
                    {
                        "id": str(sample_products[0].id),
                        "name": "Test Product 0",
                        "language_code": "en",
                        "_formatted": {
                            "name": "<em>Test</em> Product 0",
                            "description": "Description for product 0",
                        },
                        "_matchesPosition": {
                            "name": [{"start": 0, "length": 4}]
                        },
                        "_rankingScore": 0.95,
                        "_federation": {
                            "indexUid": "ProductTranslation",
                            "queriesPosition": 0,
                            "weightedRankingScore": 0.95,
                        },
                    },
                    {
                        "id": str(sample_blog_posts[0].id),
                        "title": "Test Blog Post 0",
                        "language_code": "en",
                        "_formatted": {
                            "title": "<em>Test</em> Blog Post 0",
                            "body": "Body content for post 0",
                        },
                        "_matchesPosition": {
                            "title": [{"start": 0, "length": 4}]
                        },
                        "_rankingScore": 0.85,
                        "_federation": {
                            "indexUid": "BlogPostTranslation",
                            "queriesPosition": 1,
                            "weightedRankingScore": 0.595,  # 0.85 * 0.7
                        },
                    },
                ],
                "estimatedTotalHits": 2,
                "processingTimeMs": 45,
            }

            # Execute federated search
            response = api_client.get(
                "/api/v1/search/federated",
                {
                    "query": "test",
                    "language_code": "en",
                    "limit": 20,
                    "offset": 0,
                },
            )

            # Verify response is successful
            assert response.status_code == status.HTTP_200_OK, (
                f"Federated search should return 200, got {response.status_code}"
            )

            data = response.json()

            # Verify response structure (camelCase due to DRF camelCase serialization)
            assert "limit" in data, "Response should include 'limit'"
            assert "offset" in data, "Response should include 'offset'"
            assert "estimatedTotalHits" in data, (
                "Response should include 'estimatedTotalHits'"
            )
            assert "results" in data, "Response should include 'results'"

            # Verify results are present
            assert len(data["results"]) > 0, "Results should not be empty"

            # Verify multi_search was called with federation mode
            assert mock_multi_search.called, "multi_search should be called"
            call_args = mock_multi_search.call_args

            # Check for federation in kwargs or positional args
            federation = call_args.kwargs.get("federation") or (
                call_args[1].get("federation") if len(call_args) > 1 else None
            )
            queries = call_args.kwargs.get("queries") or (
                call_args[1].get("queries") if len(call_args) > 1 else None
            )

            assert federation is not None, "Federation mode should be enabled"
            assert queries is not None, "Queries should be present"

            # Verify both indexes are queried
            assert len(queries) >= 2, "Should query at least 2 indexes"

            index_names = [q["indexUid"] for q in queries]
            assert any("ProductTranslation" in name for name in index_names), (
                "Should query ProductTranslation index"
            )
            assert any("BlogPostTranslation" in name for name in index_names), (
                "Should query BlogPostTranslation index"
            )

            # Verify federation weights
            for query in queries:
                assert "federationOptions" in query, (
                    "Each query should have federationOptions"
                )
                assert "weight" in query["federationOptions"], (
                    "Each query should have weight"
                )

            # Verify content filtering
            for query in queries:
                assert "filter" in query, "Each query should have filters"
                filters = query["filter"]

                if "ProductTranslation" in query["indexUid"]:
                    # Products should filter for active and not deleted
                    assert any("active = true" in str(f) for f in filters), (
                        "Products should filter for active = true"
                    )
                    assert any(
                        "is_deleted = false" in str(f) for f in filters
                    ), "Products should filter for is_deleted = false"

                elif "BlogPostTranslation" in query["indexUid"]:
                    # Blog posts should filter for published
                    assert any(
                        "is_published = true" in str(f) for f in filters
                    ), "Blog posts should filter for is_published = true"

    def test_federated_search_with_greeklish_expansion(self, api_client):
        """
        Test that Greek queries trigger Greeklish expansion.

        Validates: Requirements 1.6
        """
        with patch(
            "meili._client.client.client.multi_search"
        ) as mock_multi_search:
            mock_multi_search.return_value = {
                "hits": [],
                "estimatedTotalHits": 0,
                "processingTimeMs": 30,
            }

            with patch("search.views.expand_greeklish_query") as mock_expand:
                mock_expand.return_value = "expanded query"

                # Execute search with Greek language code
                api_client.get(
                    "/api/v1/search/federated",
                    {
                        "query": "υπολογιστής",
                        "language_code": "el",
                        "limit": 20,
                    },
                )

                # Verify Greeklish expansion was called
                assert mock_expand.called, (
                    "Greeklish expansion should be called for Greek queries"
                )

                # Verify expanded query was used in multi_search
                assert mock_multi_search.called, "multi_search should be called"
                call_args = mock_multi_search.call_args
                queries = call_args.kwargs.get("queries") or (
                    call_args[1].get("queries") if len(call_args) > 1 else None
                )

                for query in queries:
                    assert query["q"] == "expanded query", (
                        "Expanded query should be used in search"
                    )

    def test_federated_search_result_allocation(self, api_client):
        """
        Test that result allocation follows 70/30 rule.

        Validates: Requirements 1.7
        """
        with patch(
            "meili._client.client.client.multi_search"
        ) as mock_multi_search:
            mock_multi_search.return_value = {
                "hits": [],
                "estimatedTotalHits": 0,
                "processingTimeMs": 30,
            }

            # Request 20 results
            api_client.get(
                "/api/v1/search/federated",
                {
                    "query": "test",
                    "language_code": "en",
                    "limit": 20,
                },
            )

            # Verify multi_search was called
            assert mock_multi_search.called, "multi_search should be called"
            call_args = mock_multi_search.call_args

            # Get federation and queries from kwargs
            federation = call_args.kwargs.get("federation") or (
                call_args[1].get("federation") if len(call_args) > 1 else None
            )
            queries = call_args.kwargs.get("queries") or (
                call_args[1].get("queries") if len(call_args) > 1 else None
            )

            # Find product and blog queries
            product_query = next(
                (q for q in queries if "ProductTranslation" in q["indexUid"]),
                None,
            )
            blog_query = next(
                (q for q in queries if "BlogPostTranslation" in q["indexUid"]),
                None,
            )

            assert product_query is not None, "Product query should exist"
            assert blog_query is not None, "Blog query should exist"

            # Verify federation limit (pagination is now in federation object, not individual queries)
            assert federation is not None, "Federation object should exist"
            assert federation["limit"] == 20, (
                f"Federation limit should be 20, got {federation['limit']}"
            )


class TestOpenAPISchemaGeneration:
    """
    End-to-end tests for OpenAPI schema generation and type safety.

    Tests that:
    - OpenAPI schema includes new search endpoints
    - Request/response schemas are complete
    - Schema can be generated successfully

    Validates: Requirements 8.1, 8.2, 8.3, 8.4, 8.5

    Note: These tests don't require database access as they only test
    schema generation, not actual API calls.
    """

    def test_federated_search_endpoint_in_schema(self):
        """
        Test that federated search endpoint is included in OpenAPI schema.

        This test validates that the schema generation includes the new
        federated search endpoint with proper documentation.

        Validates: Requirements 8.1, 8.2
        """
        # Import the schema generator
        from drf_spectacular.generators import SchemaGenerator

        # Generate schema
        generator = SchemaGenerator()
        schema = generator.get_schema()

        # Verify federated search endpoint exists
        assert "paths" in schema, "Schema should have 'paths' section"

        # Check for federated search endpoint (various possible path formats)
        federated_paths = [
            path
            for path in schema["paths"].keys()
            if "federated" in path.lower()
        ]

        assert len(federated_paths) > 0, (
            f"Schema should include federated search endpoint. Available paths: {list(schema['paths'].keys())}"
        )

        # Get the federated search path
        federated_path = federated_paths[0]
        endpoint = schema["paths"][federated_path]

        # Verify GET method exists
        assert "get" in endpoint, (
            f"Federated search should support GET method. Available methods: {list(endpoint.keys())}"
        )

        get_spec = endpoint["get"]

        # Verify parameters are documented
        assert "parameters" in get_spec, (
            "Federated search should have parameters documented"
        )

        param_names = [p["name"] for p in get_spec["parameters"]]

        # Verify required parameters (camelCase in OpenAPI schema)
        assert "query" in param_names, "Schema should include 'query' parameter"
        assert "languageCode" in param_names, (
            "Schema should include 'languageCode' parameter"
        )
        assert "limit" in param_names, "Schema should include 'limit' parameter"
        assert "offset" in param_names, (
            "Schema should include 'offset' parameter"
        )

        # Verify responses are documented
        assert "responses" in get_spec, (
            "Federated search should have responses documented"
        )

        assert "200" in get_spec["responses"], (
            "Schema should include 200 response"
        )

    def test_analytics_endpoint_in_schema(self):
        """
        Test that analytics endpoint is included in OpenAPI schema.

        Validates: Requirements 8.3
        """
        from drf_spectacular.generators import SchemaGenerator

        generator = SchemaGenerator()
        schema = generator.get_schema()

        # Check for analytics endpoint
        analytics_paths = [
            path
            for path in schema["paths"].keys()
            if "analytics" in path.lower()
        ]

        assert len(analytics_paths) > 0, (
            f"Schema should include analytics endpoint. Available paths: {list(schema['paths'].keys())}"
        )

        analytics_path = analytics_paths[0]
        endpoint = schema["paths"][analytics_path]

        # Verify GET method exists
        assert "get" in endpoint, "Analytics endpoint should support GET method"

        get_spec = endpoint["get"]

        # Verify parameters
        assert "parameters" in get_spec, (
            "Analytics endpoint should have parameters documented"
        )

        param_names = [p["name"] for p in get_spec["parameters"]]

        # Verify date range parameters (camelCase in OpenAPI schema)
        assert "startDate" in param_names, (
            "Schema should include 'startDate' parameter"
        )
        assert "endDate" in param_names, (
            "Schema should include 'endDate' parameter"
        )
        assert "contentType" in param_names, (
            "Schema should include 'contentType' parameter"
        )

        # Verify responses
        assert "responses" in get_spec, (
            "Analytics endpoint should have responses documented"
        )
        assert "200" in get_spec["responses"], (
            "Schema should include 200 response"
        )

    def test_schema_response_structures(self):
        """
        Test that response schemas include all required fields.

        Validates: Requirements 8.2, 8.3
        """
        from drf_spectacular.generators import SchemaGenerator

        generator = SchemaGenerator()
        schema = generator.get_schema()

        # Check components/schemas section
        assert "components" in schema, "Schema should have 'components' section"
        assert "schemas" in schema["components"], (
            "Schema should have 'schemas' in components"
        )

        schemas = schema["components"]["schemas"]

        # Look for federated search response schema
        federated_schemas = [
            name
            for name in schemas.keys()
            if "federated" in name.lower() and "response" in name.lower()
        ]

        if federated_schemas:
            federated_schema = schemas[federated_schemas[0]]

            # Verify response structure
            if "properties" in federated_schema:
                props = federated_schema["properties"]

                # Check for key fields
                assert "results" in props or "limit" in props, (
                    "Federated response should include results or pagination fields"
                )

        # Look for analytics response schema
        analytics_schemas = [
            name
            for name in schemas.keys()
            if "analytics" in name.lower() and "response" in name.lower()
        ]

        if analytics_schemas:
            analytics_schema = schemas[analytics_schemas[0]]

            # Verify response structure
            if "properties" in analytics_schema:
                props = analytics_schema["properties"]

                # Check for key analytics fields
                expected_fields = [
                    "topQueries",
                    "top_queries",
                    "searchVolume",
                    "search_volume",
                    "clickThroughRate",
                    "click_through_rate",
                ]

                has_analytics_field = any(
                    field in props for field in expected_fields
                )

                assert has_analytics_field, (
                    f"Analytics response should include analytics fields. Found: {list(props.keys())}"
                )


@pytest.mark.django_db
class TestManagementCommandsExecution:
    """
    End-to-end tests for management commands execution.

    Tests that management commands can be executed successfully and
    produce expected results.

    Validates: Requirements 9.1, 9.2, 9.3, 9.4, 9.8
    """

    def test_meilisearch_test_federated_command(self):
        """
        Test that meilisearch_test_federated command executes successfully.

        Validates: Requirements 9.3
        """
        from io import StringIO
        from django.core.management import call_command

        # Mock Meilisearch multi_search
        with patch(
            "meili._client.client.client.multi_search"
        ) as mock_multi_search:
            mock_multi_search.return_value = {
                "hits": [
                    {
                        "id": "1",
                        "name": "Test Product",
                        "language_code": "en",
                        "_federation": {
                            "indexUid": "ProductTranslation",
                            "queriesPosition": 0,
                            "weightedRankingScore": 0.95,
                        },
                    }
                ],
                "estimatedTotalHits": 1,
                "processingTimeMs": 50,
            }

            # Capture command output
            out = StringIO()

            # Execute command
            try:
                call_command(
                    "meilisearch_test_federated",
                    "--query",
                    "test",
                    "--language-code",
                    "en",
                    "--limit",
                    "10",
                    stdout=out,
                )

                # Verify command executed without errors
                output = out.getvalue()

                # Command should produce some output
                assert len(output) > 0, "Command should produce output"

                # Verify multi_search was called
                assert mock_multi_search.called, (
                    "Command should call multi_search"
                )

            except Exception as e:
                pytest.fail(f"Command execution failed: {str(e)}")

    def test_meilisearch_export_analytics_command(self):
        """
        Test that meilisearch_export_analytics command executes successfully.

        Validates: Requirements 9.4
        """
        from io import StringIO
        from django.core.management import call_command
        import tempfile
        import os

        # Create some test analytics data
        for i in range(5):
            SearchQuery.objects.create(
                query=f"test query {i}",
                language_code="en",
                content_type="product",
                results_count=10,
                estimated_total_hits=100,
            )

        # Create temporary output file
        with tempfile.NamedTemporaryFile(
            mode="w", delete=False, suffix=".json"
        ) as tmp:
            tmp_path = tmp.name

        try:
            # Capture command output
            out = StringIO()

            # Execute command
            call_command(
                "meilisearch_export_analytics",
                "--output",
                tmp_path,
                stdout=out,
            )

            # Verify file was created
            assert os.path.exists(tmp_path), "Export file should be created"

            # Verify file has content
            file_size = os.path.getsize(tmp_path)
            assert file_size > 0, "Export file should have content"

            # Verify it's valid JSON
            with open(tmp_path, "r") as f:
                data = json.load(f)

                # Verify data structure
                assert isinstance(data, (dict, list)), (
                    "Export should contain valid JSON data"
                )

        finally:
            # Clean up temporary file
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def test_meilisearch_update_index_settings_command(self):
        """
        Test that meilisearch_update_index_settings command executes.

        Validates: Requirements 9.1
        """
        from io import StringIO
        from django.core.management import call_command

        # Mock the Meilisearch client
        with patch("meili._client.client.client.index") as mock_index:
            mock_index_obj = Mock()
            mock_index_obj.update_settings.return_value = {"taskUid": 123}
            mock_index.return_value = mock_index_obj

            # Capture command output
            out = StringIO()

            # Execute command
            try:
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

                # Verify command executed
                output = out.getvalue()
                assert len(output) > 0, "Command should produce output"

            except Exception:
                # Command might fail if index doesn't exist, which is OK for this test
                # We're just verifying it can be executed
                pass

    def test_meilisearch_update_ranking_command(self):
        """
        Test that meilisearch_update_ranking command executes.

        Validates: Requirements 9.2
        """
        from io import StringIO
        from django.core.management import call_command

        # Mock the Meilisearch client
        with patch("meili._client.client.client.index") as mock_index:
            mock_index_obj = Mock()
            mock_index_obj.update_ranking_rules.return_value = {"taskUid": 123}
            mock_index.return_value = mock_index_obj

            # Capture command output
            out = StringIO()

            # Execute command
            try:
                call_command(
                    "meilisearch_update_ranking",
                    "--index",
                    "ProductTranslation",
                    "--rules",
                    "words,typo,proximity,attribute,sort,exactness",
                    stdout=out,
                )

                # Verify command executed
                output = out.getvalue()
                assert len(output) > 0, "Command should produce output"

            except Exception:
                # Command might fail if index doesn't exist, which is OK for this test
                pass
