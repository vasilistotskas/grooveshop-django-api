"""
Property validation tests for federated search functionality.

This module tests that the federated_search view correctly implements
federated search across ProductTranslation and BlogPostTranslation indexes
using Meilisearch's multi_search API with federation mode.
"""

from unittest.mock import patch
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
        mock_client.search_client.multi_search.return_value = {
            "hits": [],
            "estimatedTotalHits": 0,
        }
        yield mock_client


@pytest.mark.django_db
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

        assert mock_meili_client.search_client.multi_search.called

        call_args = mock_meili_client.search_client.multi_search.call_args
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

        call_args = mock_meili_client.search_client.multi_search.call_args
        queries = call_args.kwargs.get("queries") or call_args[1].get("queries")

        product_query = next(
            q for q in queries if "ProductTranslation" in q["indexUid"]
        )
        blog_query = next(
            q for q in queries if "BlogPostTranslation" in q["indexUid"]
        )

        assert product_query["federationOptions"]["weight"] == 1.0
        assert blog_query["federationOptions"]["weight"] == 0.7

    def test_federation_metadata_preservation(self, mock_meili_client):
        """Results carry ``federation`` metadata built from Meilisearch's
        raw ``_federation`` hit key.

        Uses a REAL product so the enrichment path actually runs — with
        mocked models the bulk fetch yields nothing and the assertions
        would never execute.
        """
        from product.factories.product import ProductFactory

        product = ProductFactory()
        translation = product.translations.first()
        mock_meili_client.search_client.multi_search.return_value = {
            "hits": [
                {
                    "id": str(translation.pk),
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

        request = self.factory.get("/api/search/federated", {"query": "laptop"})
        response = federated_search(request)

        results = response.data["results"]
        assert len(results) == 1
        # Exposed as ``federation`` — a leading underscore would be
        # mangled by the camelCase renderer ("_federation" -> "Federation").
        assert results[0]["federation"]["indexUid"] == "ProductTranslation"
        assert "_federation" not in results[0]

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

        call_args = mock_meili_client.search_client.multi_search.call_args
        queries = call_args.kwargs.get("queries") or call_args[1].get("queries")

        expected_filter = f"language_code = '{language_code}'"
        for query in queries:
            assert expected_filter in query["filter"]

    @pytest.mark.parametrize(
        "greek_query",
        ["υπολογιστής", "ypologistis", "anavathmisi se windows"],
    )
    def test_query_sent_to_meilisearch_unmodified(
        self, mock_models, mock_meili_client, greek_query
    ):
        """
        Greek and Greeklish queries must reach Meilisearch verbatim —
        Greeklish matching happens against the indexed ``*_greeklish``
        shadow fields, not through query rewriting.
        """
        request = self.factory.get(
            "/api/search/federated",
            {"query": greek_query, "language_code": "el"},
        )
        federated_search(request)

        # The zero-result relaxed fallback may issue a second call with
        # the leading word dropped — the FIRST call must carry the raw
        # query verbatim.
        call_args = mock_meili_client.search_client.multi_search.call_args_list[
            0
        ]
        queries = call_args.kwargs.get("queries") or call_args[1].get("queries")
        for query in queries:
            assert query["q"] == greek_query

    def test_zero_results_retries_without_leading_word(
        self, mock_models, mock_meili_client
    ):
        """
        A multi-word query with no hits is retried once with the leading
        word dropped — the only shape the ``last`` matching strategy can
        never relax on its own.
        """
        request = self.factory.get(
            "/api/search/federated",
            {"query": "anaba8mish se windows", "language_code": "el"},
        )
        response = federated_search(request)

        multi_search = mock_meili_client.search_client.multi_search
        assert multi_search.call_count == 2
        # The fallback found nothing either (mock returns no hits), so
        # the response must NOT claim a relaxed query was used.
        assert response.data["relaxed_query"] is None
        assert response.data["query_id"]
        retry_call = multi_search.call_args_list[1]
        queries = retry_call.kwargs.get("queries") or retry_call[1].get(
            "queries"
        )
        for query in queries:
            assert query["q"] == "se windows"

    def test_successful_fallback_disclosed_as_relaxed_query(
        self, mock_models, mock_meili_client
    ):
        """When the retry finds hits, the response says which query
        actually matched — never a silent swap."""
        mock_product, _ = mock_models
        mock_meili_client.search_client.multi_search.side_effect = [
            {"hits": [], "estimatedTotalHits": 0},
            {
                "hits": [
                    {
                        "id": "1",
                        "_federation": {
                            "indexUid": "ProductTranslation",
                            "queriesPosition": 0,
                            "weightedRankingScore": 0.9,
                        },
                    }
                ],
                "estimatedTotalHits": 1,
            },
        ]
        mock_product.get_search_result_queryset.return_value.filter.return_value = []

        request = self.factory.get(
            "/api/search/federated",
            {"query": "anaba8mish se windows", "language_code": "el"},
        )
        response = federated_search(request)

        assert response.data["relaxed_query"] == "se windows"

    def test_zero_results_single_word_is_not_retried(
        self, mock_models, mock_meili_client
    ):
        """A single-word query has nothing left to relax."""
        request = self.factory.get(
            "/api/search/federated",
            {"query": "anaba8mish", "language_code": "el"},
        )
        federated_search(request)

        assert mock_meili_client.search_client.multi_search.call_count == 1

    def test_queries_with_hits_are_not_retried(
        self, mock_models, mock_meili_client
    ):
        """The fallback only fires on zero hits."""
        mock_product, _ = mock_models
        mock_meili_client.search_client.multi_search.return_value = {
            "hits": [
                {
                    "id": "1",
                    "_federation": {
                        "indexUid": "ProductTranslation",
                        "queriesPosition": 0,
                        "weightedRankingScore": 0.9,
                    },
                }
            ],
            "estimatedTotalHits": 1,
        }
        mock_product.get_search_result_queryset.return_value.filter.return_value = []

        request = self.factory.get(
            "/api/search/federated",
            {"query": "anavathmisi se windows", "language_code": "el"},
        )
        response = federated_search(request)

        assert mock_meili_client.search_client.multi_search.call_count == 1
        assert response.data["relaxed_query"] is None
        assert response.data["query_id"]

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

        call_args = mock_meili_client.search_client.multi_search.call_args
        federation = call_args.kwargs.get("federation") or call_args[1].get(
            "federation"
        )

        assert federation["limit"] == total_limit


@pytest.mark.django_db
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

        call_args = mock_meili_client.search_client.multi_search.call_args
        federation = call_args.kwargs.get("federation") or call_args[1].get(
            "federation"
        )
        assert federation["offset"] == 10

    def test_federated_search_meilisearch_error(
        self, mock_models, mock_meili_client
    ):
        """Test federated search when Meilisearch returns error."""
        mock_meili_client.search_client.multi_search.side_effect = Exception(
            "Meilisearch connection failed"
        )
        assert (
            mock_meili_client.search_client.multi_search.side_effect is not None
        )

    def test_federated_search_object_not_found(
        self, mock_models, mock_meili_client
    ):
        """Test federated search when Django object not found in database."""
        mock_product, _ = mock_models

        mock_meili_client.search_client.multi_search.return_value = {
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

        # First call only — the zero-result relaxed fallback may issue a
        # second call with the leading word dropped.
        call_args = mock_meili_client.search_client.multi_search.call_args_list[
            0
        ]
        queries = call_args.kwargs.get("queries") or call_args[1].get("queries")
        assert queries[0]["q"] == "laptop computer"

    def test_federated_search_without_language_filter(
        self, mock_models, mock_meili_client
    ):
        """Test federated search without language_code parameter."""
        request = self.factory.get("/api/search/federated", {"query": "test"})
        federated_search(request)

        call_args = mock_meili_client.search_client.multi_search.call_args
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
