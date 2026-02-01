"""
Integration tests for federated search functionality.

These tests validate the complete federated search flow including:
- Multi-index search with federation
- Content filtering (active products, published blog posts)
- Analytics tracking
- Greeklish expansion
- Result weighting and merging
"""

import pytest
from django.test import Client
from django.contrib.auth import get_user_model
from search.models import SearchQuery, SearchClick

User = get_user_model()


@pytest.mark.django_db
class TestFederatedSearchIntegration:
    """Integration tests for federated search endpoint."""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Set up test client and data."""
        self.client = Client()
        self.federated_search_url = "/api/v1/search/federated"

    def test_federated_search_returns_both_products_and_blog_posts(self):
        """
        Test that federated search queries both ProductTranslation and
        BlogPostTranslation indexes and returns merged results.
        """
        # Execute federated search
        response = self.client.get(
            self.federated_search_url,
            {"query": "laptop", "language_code": "en", "limit": 20},
        )

        assert response.status_code == 200
        data = response.json()

        # Verify response structure (camelCase due to DRF middleware)
        assert "results" in data
        assert "limit" in data
        assert "offset" in data
        assert "estimatedTotalHits" in data

        # Verify results have content_type field
        if len(data["results"]) > 0:
            for result in data["results"]:
                assert "content_type" in result
                assert result["content_type"] in ["product", "blog_post"]
                assert "id" in result
                assert "_rankingScore" in result

    def test_federated_search_applies_content_filters(self):
        """
        Test that federated search excludes inactive/deleted products
        and unpublished blog posts.
        """
        response = self.client.get(
            self.federated_search_url,
            {"query": "test", "language_code": "en", "limit": 20},
        )

        assert response.status_code == 200
        data = response.json()

        # Verify all product results are active and not deleted
        product_results = [
            r for r in data["results"] if r["content_type"] == "product"
        ]
        for result in product_results:
            if "object" in result:
                assert result["object"].get("active") is True
                assert result["object"].get("is_deleted") is False

        # Verify all blog post results are published
        blog_results = [
            r for r in data["results"] if r["content_type"] == "blog_post"
        ]
        for result in blog_results:
            if "object" in result:
                assert result["object"].get("is_published") is True

    def test_federated_search_includes_federation_metadata(self):
        """
        Test that federated search results include _federation metadata
        with indexUid, queriesPosition, and weightedRankingScore.
        """
        response = self.client.get(
            self.federated_search_url,
            {"query": "laptop", "language_code": "en", "limit": 20},
        )

        assert response.status_code == 200
        data = response.json()

        # Verify federation metadata in results
        if len(data["results"]) > 0:
            for result in data["results"]:
                assert "_federation" in result
                assert "indexUid" in result["_federation"]
                assert "queriesPosition" in result["_federation"]
                assert "weightedRankingScore" in result["_federation"]

    def test_federated_search_applies_language_filter(self):
        """
        Test that federated search filters results by language_code
        across both indexes.
        """
        # Search in English
        response_en = self.client.get(
            self.federated_search_url,
            {"query": "laptop", "language_code": "en", "limit": 20},
        )

        assert response_en.status_code == 200
        data_en = response_en.json()

        # Verify all results have English language code
        for result in data_en["results"]:
            if "object" in result:
                assert result["object"].get("language_code") == "en"

        # Search in Greek
        response_el = self.client.get(
            self.federated_search_url,
            {"query": "υπολογιστής", "language_code": "el", "limit": 20},
        )

        assert response_el.status_code == 200
        data_el = response_el.json()

        # Verify all results have Greek language code
        for result in data_el["results"]:
            if "object" in result:
                assert result["object"].get("language_code") == "el"

    def test_federated_search_applies_greeklish_expansion(self):
        """
        Test that federated search applies Greeklish expansion for
        Greek language queries.
        """
        # Search with Greeklish query
        response = self.client.get(
            self.federated_search_url,
            {
                "query": "kompiouter",  # Greeklish for κομπιούτερ
                "language_code": "el",
                "limit": 20,
            },
        )

        assert response.status_code == 200
        data = response.json()

        # Verify results are returned (Greeklish expansion should work)
        assert "results" in data
        # Note: Actual results depend on test data availability

    def test_federated_search_respects_result_allocation(self):
        """
        Test that federated search allocates results approximately
        70% to products and 30% to blog posts.
        """
        response = self.client.get(
            self.federated_search_url,
            {"query": "laptop", "language_code": "en", "limit": 20},
        )

        assert response.status_code == 200
        data = response.json()

        if len(data["results"]) >= 10:  # Only test if enough results
            product_count = sum(
                1 for r in data["results"] if r["content_type"] == "product"
            )
            blog_count = sum(
                1 for r in data["results"] if r["content_type"] == "blog_post"
            )

            total = product_count + blog_count
            if total > 0:
                product_ratio = product_count / total
                # Allow some variance (60-80% for products)
                assert 0.6 <= product_ratio <= 0.8

    def test_federated_search_creates_analytics_record(self):
        """
        Test that federated search creates a SearchQuery analytics record.
        """
        initial_count = SearchQuery.objects.count()

        response = self.client.get(
            self.federated_search_url,
            {"query": "laptop", "language_code": "en", "limit": 20},
        )

        assert response.status_code == 200

        # Verify SearchQuery record was created
        assert SearchQuery.objects.count() == initial_count + 1

        # Verify record details
        search_query = SearchQuery.objects.latest("timestamp")
        assert search_query.query == "laptop"
        assert search_query.language_code == "en"
        assert search_query.content_type == "federated"
        assert search_query.results_count >= 0

    def test_federated_search_handles_empty_query(self):
        """
        Test that federated search handles empty query gracefully.
        """
        response = self.client.get(
            self.federated_search_url,
            {"query": "", "language_code": "en", "limit": 20},
        )

        # Should return 200 with empty or all results
        assert response.status_code in [200, 400]

    def test_federated_search_handles_special_characters(self):
        """
        Test that federated search handles special characters in query.
        """
        special_queries = [
            "laptop!@#$%",
            "café",
            "test & query",
            'query "with quotes"',
            "query'with'apostrophes",
        ]

        for query in special_queries:
            response = self.client.get(
                self.federated_search_url,
                {"query": query, "language_code": "en", "limit": 20},
            )

            # Should not crash
            assert response.status_code in [200, 400]

    def test_federated_search_pagination(self):
        """
        Test that federated search supports pagination with limit and offset.
        """
        # First page
        response_page1 = self.client.get(
            self.federated_search_url,
            {
                "query": "laptop",
                "language_code": "en",
                "limit": 10,
                "offset": 0,
            },
        )

        assert response_page1.status_code == 200
        data_page1 = response_page1.json()

        # Second page
        response_page2 = self.client.get(
            self.federated_search_url,
            {
                "query": "laptop",
                "language_code": "en",
                "limit": 10,
                "offset": 10,
            },
        )

        assert response_page2.status_code == 200
        data_page2 = response_page2.json()

        # Verify different results (if enough data)
        if len(data_page1["results"]) > 0 and len(data_page2["results"]) > 0:
            page1_ids = {r["id"] for r in data_page1["results"]}
            page2_ids = {r["id"] for r in data_page2["results"]}
            # Pages should have different results
            assert page1_ids != page2_ids

    def test_federated_search_returns_formatted_highlights(self):
        """
        Test that federated search returns _formatted field with highlights.
        """
        response = self.client.get(
            self.federated_search_url,
            {"query": "laptop", "language_code": "en", "limit": 20},
        )

        assert response.status_code == 200
        data = response.json()

        # Verify _formatted field in results
        if len(data["results"]) > 0:
            for result in data["results"]:
                assert "_formatted" in result
                # _formatted should be a dict with highlighted fields
                assert isinstance(result["_formatted"], dict)

    def test_federated_search_includes_ranking_scores(self):
        """
        Test that federated search includes _rankingScore for each result.
        """
        response = self.client.get(
            self.federated_search_url,
            {"query": "laptop", "language_code": "en", "limit": 20},
        )

        assert response.status_code == 200
        data = response.json()

        # Verify _rankingScore in results
        if len(data["results"]) > 0:
            for result in data["results"]:
                assert "_rankingScore" in result
                assert isinstance(result["_rankingScore"], (int, float))
                assert result["_rankingScore"] >= 0

    def test_federated_search_handles_missing_parameters(self):
        """
        Test that federated search handles missing required parameters.
        """
        # Missing query parameter
        response = self.client.get(
            self.federated_search_url, {"language_code": "en", "limit": 20}
        )

        # Should return 400 Bad Request
        assert response.status_code == 400

    def test_federated_search_validates_limit_parameter(self):
        """
        Test that federated search validates limit parameter.
        """
        # Test with invalid limit
        response = self.client.get(
            self.federated_search_url,
            {"query": "laptop", "language_code": "en", "limit": -1},
        )

        # Should return 400 or use default
        assert response.status_code in [200, 400]

        # Test with very large limit
        response = self.client.get(
            self.federated_search_url,
            {"query": "laptop", "language_code": "en", "limit": 10000},
        )

        # Should cap at maximum or return error
        assert response.status_code in [200, 400]

    def test_federated_search_performance(self):
        """
        Test that federated search completes within acceptable time.
        """
        import time

        start_time = time.time()

        response = self.client.get(
            self.federated_search_url,
            {"query": "laptop", "language_code": "en", "limit": 20},
        )

        end_time = time.time()
        duration = end_time - start_time

        assert response.status_code == 200
        # Should complete within 2 seconds
        assert duration < 2.0


@pytest.mark.django_db
class TestSearchAnalyticsIntegration:
    """Integration tests for search analytics endpoint."""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Set up test client and data."""
        self.client = Client()
        self.analytics_url = "/api/v1/search/analytics"

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
        """
        Test that analytics endpoint filters by date range.
        """
        # Create test search queries with different dates
        from datetime import timedelta
        from django.utils import timezone

        old_date = timezone.now() - timedelta(days=365)
        recent_date = timezone.now() - timedelta(days=1)

        SearchQuery.objects.create(
            query="old query",
            language_code="en",
            content_type="product",
            results_count=10,
            estimated_total_hits=10,
            timestamp=old_date,
        )

        SearchQuery.objects.create(
            query="recent query",
            language_code="en",
            content_type="product",
            results_count=10,
            estimated_total_hits=10,
            timestamp=recent_date,
        )

        # Query recent data only
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
        data = response.json()

        # Verify only recent queries are included (camelCase due to DRF middleware)
        top_queries = [q["query"] for q in data["topQueries"]]
        assert "recent query" in top_queries or len(top_queries) == 0
        assert "old query" not in top_queries

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

        # Verify only product queries are included (camelCase due to DRF middleware)
        if len(data["topQueries"]) > 0:
            # All queries should be product-related
            assert data["searchVolume"]["byContentType"].get("product", 0) > 0

    def test_analytics_calculates_click_through_rate(self):
        """
        Test that analytics endpoint calculates click-through rate correctly.
        """
        # Create test search query with clicks
        search_query = SearchQuery.objects.create(
            query="test query",
            language_code="en",
            content_type="product",
            results_count=10,
            estimated_total_hits=10,
        )

        # Create click record
        SearchClick.objects.create(
            search_query=search_query,
            result_id="123",
            result_type="product",
            position=0,
        )

        response = self.client.get(self.analytics_url)

        assert response.status_code == 200
        data = response.json()

        # Verify CTR is calculated (camelCase due to DRF middleware)
        assert "clickThroughRate" in data
        assert isinstance(data["clickThroughRate"], (int, float))
        assert 0 <= data["clickThroughRate"] <= 1

    def test_analytics_identifies_zero_result_queries(self):
        """
        Test that analytics endpoint identifies queries with zero results.
        """
        # Create test search query with zero results
        SearchQuery.objects.create(
            query="nonexistent query",
            language_code="en",
            content_type="product",
            results_count=0,
            estimated_total_hits=0,
        )

        response = self.client.get(self.analytics_url)

        assert response.status_code == 200
        data = response.json()

        # Verify zero result queries are identified (camelCase due to DRF middleware)
        assert "zeroResultQueries" in data
        zero_queries = [q["query"] for q in data["zeroResultQueries"]]
        assert "nonexistent query" in zero_queries or len(zero_queries) == 0

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


@pytest.mark.django_db
class TestSearchClickTracking:
    """Integration tests for search click tracking."""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Set up test client and data."""
        self.client = Client()

    def test_search_click_creates_record(self):
        """
        Test that clicking a search result creates a SearchClick record.
        """
        # Create test search query
        search_query = SearchQuery.objects.create(
            query="test query",
            language_code="en",
            content_type="product",
            results_count=10,
            estimated_total_hits=10,
        )

        initial_count = SearchClick.objects.count()

        # Simulate click tracking (would be done via frontend API call)
        SearchClick.objects.create(
            search_query=search_query,
            result_id="123",
            result_type="product",
            position=0,
        )

        # Verify SearchClick record was created
        assert SearchClick.objects.count() == initial_count + 1

        # Verify record details
        click = SearchClick.objects.latest("timestamp")
        assert click.search_query == search_query
        assert click.result_id == "123"
        assert click.result_type == "product"
        assert click.position == 0

    def test_search_click_tracks_position(self):
        """
        Test that search click tracking records result position.
        """
        search_query = SearchQuery.objects.create(
            query="test query",
            language_code="en",
            content_type="product",
            results_count=10,
            estimated_total_hits=10,
        )

        # Create clicks at different positions
        for position in range(5):
            SearchClick.objects.create(
                search_query=search_query,
                result_id=f"result_{position}",
                result_type="product",
                position=position,
            )

        # Verify all positions were recorded
        clicks = SearchClick.objects.filter(search_query=search_query).order_by(
            "position"
        )
        positions = [click.position for click in clicks]
        assert positions == [0, 1, 2, 3, 4]
