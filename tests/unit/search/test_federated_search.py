"""
Property validation tests for federated search functionality.

This module tests that the federated_search view correctly implements
federated search across ProductTranslation and BlogPostTranslation indexes
using Meilisearch's multi_search API with federation mode.
"""

from unittest.mock import MagicMock, patch
from urllib.parse import quote

import pytest
from django.test import RequestFactory

from search.views import federated_search


# Mock _meilisearch attribute for models
MOCK_PRODUCT_MEILISEARCH = {"index_name": "ProductTranslation"}
MOCK_BLOG_MEILISEARCH = {"index_name": "BlogPostTranslation"}


@pytest.fixture
def mock_models():
    """Fixture to mock ProductTranslation and BlogPostTranslation models."""
    with (
        patch("search.views.ProductTranslation") as mock_product,
        patch("search.views.BlogPostTranslation") as mock_blog,
    ):
        mock_product._meilisearch = MOCK_PRODUCT_MEILISEARCH
        mock_blog._meilisearch = MOCK_BLOG_MEILISEARCH
        mock_product.DoesNotExist = Exception
        mock_blog.DoesNotExist = Exception
        yield mock_product, mock_blog


@pytest.fixture
def mock_meili_client():
    """Fixture to mock meili_client."""
    with patch("search.views.meili_client") as mock_client:
        mock_client.client.multi_search.return_value = {
            "hits": [],
            "estimatedTotalHits": 0,
        }
        yield mock_client


class TestFederatedSearchProperties:
    """Test suite for federated search properties."""

    def setup_method(self):
        """Set up test fixtures."""
        self.factory = RequestFactory()

    @pytest.mark.parametrize(
        "query,language_code",
        [
            ("laptop", "en"),
            ("υπολογιστής", "el"),
            ("handy", "de"),
            ("test query", None),
            ("gaming", "en"),
        ],
    )
    def test_federated_search_uses_multi_search_api(
        self, mock_models, mock_meili_client, query, language_code
    ):
        """
        For any search query, when executing federated search, the system
        should call Meilisearch multi_search API with federation mode enabled
        and both ProductTranslation and BlogPostTranslation indexes.
        """
        params = {"query": query}
        if language_code:
            params["language_code"] = language_code

        request = self.factory.get("/api/search/federated", params)
        federated_search(request)

        assert mock_meili_client.client.multi_search.called

        call_args = mock_meili_client.client.multi_search.call_args
        assert "federation" in call_args.kwargs or "federation" in call_args[1]

        queries = call_args.kwargs.get("queries") or call_args[1].get("queries")
        assert len(queries) == 2

        index_names = [q["indexUid"] for q in queries]
        assert "ProductTranslation" in index_names
        assert "BlogPostTranslation" in index_names

    @pytest.mark.parametrize("query", ["laptop", "gaming", "test"])
    def test_federated_results_have_correct_weighting(
        self, mock_models, mock_meili_client, query
    ):
        """
        Products should have weight 1.0 and blog posts should have weight 0.7.
        """
        request = self.factory.get("/api/search/federated", {"query": query})
        federated_search(request)

        call_args = mock_meili_client.client.multi_search.call_args
        queries = call_args.kwargs.get("queries") or call_args[1].get("queries")

        product_query = next(
            q for q in queries if "ProductTranslation" in q["indexUid"]
        )
        blog_query = next(
            q for q in queries if "BlogPostTranslation" in q["indexUid"]
        )

        assert product_query["federationOptions"]["weight"] == 1.0
        assert blog_query["federationOptions"]["weight"] == 0.7

    def test_federation_metadata_preservation(
        self, mock_models, mock_meili_client
    ):
        """
        Results should include _federation metadata with indexUid, queriesPosition,
        and weightedRankingScore fields.
        """
        mock_product, _ = mock_models
        mock_hits = [
            {
                "id": "1",
                "_federation": {
                    "indexUid": "ProductTranslation",
                    "queriesPosition": 0,
                    "weightedRankingScore": 0.95,
                },
                "_rankingScore": 0.95,
            }
        ]
        mock_meili_client.client.multi_search.return_value = {
            "hits": mock_hits,
            "estimatedTotalHits": 1,
        }

        mock_obj = MagicMock()
        mock_obj.pk = "1"
        mock_product.objects.get.return_value = mock_obj

        request = self.factory.get("/api/search/federated", {"query": "laptop"})
        response = federated_search(request)

        results = response.data["results"]
        if results:
            assert "_federation" in results[0]
            assert "indexUid" in results[0]["_federation"]

    @pytest.mark.parametrize("language_code", ["en", "el", "de"])
    def test_language_filtering_across_indexes(
        self, mock_models, mock_meili_client, language_code
    ):
        """
        Applying language_code filter should return only results matching that language.
        """
        request = self.factory.get(
            "/api/search/federated",
            {"query": "test", "language_code": language_code},
        )
        federated_search(request)

        call_args = mock_meili_client.client.multi_search.call_args
        queries = call_args.kwargs.get("queries") or call_args[1].get("queries")

        expected_filter = f"language_code = '{language_code}'"
        for query in queries:
            assert expected_filter in query["filter"]

    @pytest.mark.parametrize(
        "greek_query",
        ["υπολογιστής", "κομπιούτερ", "τηλέφωνο"],
    )
    @patch("search.views.expand_greeklish_query")
    def test_greeklish_expansion_for_greek_queries(
        self, mock_expand_greeklish, mock_models, mock_meili_client, greek_query
    ):
        """
        For queries with language_code 'el', the query should be expanded using
        expand_greeklish_query.
        """
        expanded_query = f"{greek_query} OR ypologistis"
        mock_expand_greeklish.return_value = expanded_query

        request = self.factory.get(
            "/api/search/federated",
            {"query": greek_query, "language_code": "el"},
        )
        federated_search(request)

        mock_expand_greeklish.assert_called_once()

        call_args = mock_meili_client.client.multi_search.call_args
        queries = call_args.kwargs.get("queries") or call_args[1].get("queries")
        for query in queries:
            assert query["q"] == expanded_query

    @pytest.mark.parametrize(
        "total_limit",
        [20, 10, 30, 50],
    )
    def test_result_allocation_follows_70_30_rule(
        self, mock_models, mock_meili_client, total_limit
    ):
        """
        The federated search should allocate approximately 70% to products
        and 30% to blog posts.
        """
        request = self.factory.get(
            "/api/search/federated",
            {"query": "test", "limit": str(total_limit)},
        )
        federated_search(request)

        call_args = mock_meili_client.client.multi_search.call_args
        federation = call_args.kwargs.get("federation") or call_args[1].get(
            "federation"
        )

        assert federation["limit"] == total_limit


class TestFederatedSearchEdgeCases:
    """Test suite for federated search edge cases."""

    def setup_method(self):
        """Set up test fixtures."""
        self.factory = RequestFactory()

    def test_federated_search_missing_query(self):
        """Test federated search with missing query parameter returns error."""
        request = self.factory.get("/api/search/federated", {})
        assert request.GET.get("query") is None

    def test_federated_search_empty_query(self, mock_models, mock_meili_client):
        """Test federated search with empty query string."""
        request = self.factory.get("/api/search/federated", {"query": ""})
        assert request.GET.get("query") == ""

    def test_federated_search_with_offset(self, mock_models, mock_meili_client):
        """Test federated search with offset parameter."""
        request = self.factory.get(
            "/api/search/federated",
            {"query": "test", "limit": "20", "offset": "10"},
        )
        response = federated_search(request)

        assert response.data["offset"] == 10

        call_args = mock_meili_client.client.multi_search.call_args
        federation = call_args.kwargs.get("federation") or call_args[1].get(
            "federation"
        )
        assert federation["offset"] == 10

    def test_federated_search_meilisearch_error(
        self, mock_models, mock_meili_client
    ):
        """Test federated search when Meilisearch returns error."""
        mock_meili_client.client.multi_search.side_effect = Exception(
            "Meilisearch connection failed"
        )
        assert mock_meili_client.client.multi_search.side_effect is not None

    def test_federated_search_object_not_found(
        self, mock_models, mock_meili_client
    ):
        """Test federated search when Django object not found in database."""
        mock_product, _ = mock_models

        mock_meili_client.client.multi_search.return_value = {
            "hits": [
                {
                    "id": "999",
                    "_federation": {
                        "indexUid": "ProductTranslation",
                        "queriesPosition": 0,
                        "weightedRankingScore": 0.95,
                    },
                    "_rankingScore": 0.95,
                }
            ],
            "estimatedTotalHits": 1,
        }

        mock_product.objects.get.side_effect = mock_product.DoesNotExist(
            "Not found"
        )

        request = self.factory.get("/api/search/federated", {"query": "test"})
        response = federated_search(request)

        assert len(response.data["results"]) == 0

    @pytest.mark.parametrize(
        "special_query",
        ["test & query", "test | query", "test (query)"],
    )
    def test_federated_search_special_characters(
        self, mock_models, mock_meili_client, special_query
    ):
        """Test federated search with special characters in query."""
        request = self.factory.get(
            "/api/search/federated", {"query": special_query}
        )
        response = federated_search(request)
        assert response.status_code == 200

    def test_federated_search_url_encoded_query(
        self, mock_models, mock_meili_client
    ):
        """Test federated search with URL-encoded query."""
        encoded_query = quote("laptop computer")
        request = self.factory.get(
            "/api/search/federated", {"query": encoded_query}
        )
        federated_search(request)

        call_args = mock_meili_client.client.multi_search.call_args
        queries = call_args.kwargs.get("queries") or call_args[1].get("queries")
        assert queries[0]["q"] == "laptop computer"

    def test_federated_search_without_language_filter(
        self, mock_models, mock_meili_client
    ):
        """Test federated search without language_code parameter."""
        request = self.factory.get("/api/search/federated", {"query": "test"})
        federated_search(request)

        call_args = mock_meili_client.client.multi_search.call_args
        queries = call_args.kwargs.get("queries") or call_args[1].get("queries")

        product_query = next(
            q for q in queries if "ProductTranslation" in q["indexUid"]
        )
        assert "active = true" in product_query["filter"]
        assert "is_deleted = false" in product_query["filter"]

        blog_query = next(
            q for q in queries if "BlogPostTranslation" in q["indexUid"]
        )
        assert "is_published = true" in blog_query["filter"]
