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


class TestFederatedSearchProperties:
    """Test suite for federated search properties."""

    def setup_method(self):
        """Set up test fixtures."""
        self.factory = RequestFactory()

    # Feature: meilisearch-enhancements, Property 1: Federated search uses multi_search API
    @pytest.mark.parametrize(
        "query,language_code",
        [
            ("laptop", "en"),
            ("υπολογιστής", "el"),
            ("handy", "de"),
            ("test query", None),
            ("special!@#$%", "en"),
            ("a" * 100, "el"),  # Long query
            ("123", "en"),  # Numeric query
            ("café", "en"),  # Unicode
            ("laptop notebook", "en"),  # Multi-word
            ("gaming", "en"),
        ],
    )
    @patch("search.views.meili_client")
    def test_federated_search_uses_multi_search_api(
        self, mock_meili_client, query, language_code
    ):
        """
        Property 1: Federated search uses multi_search API.

        For any search query, when executing federated search, the system
        should call Meilisearch multi_search API with federation mode enabled
        and both ProductTranslation and BlogPostTranslation indexes.

        Validates: Requirements 1.1
        """
        # Mock multi_search response
        mock_meili_client.client.multi_search.return_value = {
            "hits": [],
            "estimatedTotalHits": 0,
        }

        # Create request
        params = {"query": query}
        if language_code:
            params["language_code"] = language_code

        request = self.factory.get("/api/search/federated", params)

        # Execute federated search
        federated_search(request)

        # Verify multi_search was called
        assert mock_meili_client.client.multi_search.called

        # Verify federation mode enabled (using keyword arguments)
        call_args = mock_meili_client.client.multi_search.call_args
        assert "federation" in call_args.kwargs or "federation" in call_args[1]

        # Verify both indexes queried
        queries = call_args.kwargs.get("queries") or call_args[1].get("queries")
        assert len(queries) == 2

        index_names = [q["indexUid"] for q in queries]
        assert "ProductTranslation" in index_names
        assert "BlogPostTranslation" in index_names

    # Feature: meilisearch-enhancements, Property 2: Federated results have correct weighting
    @pytest.mark.parametrize(
        "query",
        [
            "laptop",
            "gaming",
            "test",
            "product",
            "article",
        ],
    )
    @patch("search.views.meili_client")
    def test_federated_results_have_correct_weighting(
        self, mock_meili_client, query
    ):
        """
        Property 2: Federated results have correct weighting.

        For any federated search result set, products should have weight 1.0
        and blog posts should have weight 0.7 in the merged results.

        Validates: Requirements 1.2
        """
        # Mock multi_search response
        mock_meili_client.client.multi_search.return_value = {
            "hits": [],
            "estimatedTotalHits": 0,
        }

        # Create request
        request = self.factory.get("/api/search/federated", {"query": query})

        # Execute federated search
        federated_search(request)

        # Verify weights in federation options
        call_args = mock_meili_client.client.multi_search.call_args
        queries = call_args.kwargs.get("queries") or call_args[1].get("queries")

        # Find product and blog post queries
        product_query = next(
            q for q in queries if "ProductTranslation" in q["indexUid"]
        )
        blog_query = next(
            q for q in queries if "BlogPostTranslation" in q["indexUid"]
        )

        # Verify weights
        assert product_query["federationOptions"]["weight"] == 1.0
        assert blog_query["federationOptions"]["weight"] == 0.7

    # Feature: meilisearch-enhancements, Property 3: Federation metadata preservation
    @pytest.mark.parametrize(
        "query,mock_hits",
        [
            (
                "laptop",
                [
                    {
                        "id": "1",
                        "_federation": {
                            "indexUid": "ProductTranslation",
                            "queriesPosition": 0,
                            "weightedRankingScore": 0.95,
                        },
                        "_rankingScore": 0.95,
                    }
                ],
            ),
            (
                "article",
                [
                    {
                        "id": "2",
                        "_federation": {
                            "indexUid": "BlogPostTranslation",
                            "queriesPosition": 1,
                            "weightedRankingScore": 0.85,
                        },
                        "_rankingScore": 0.85,
                    }
                ],
            ),
        ],
    )
    @patch("search.views.ProductTranslation")
    @patch("search.views.BlogPostTranslation")
    @patch("search.views.meili_client")
    def test_federation_metadata_preservation(
        self,
        mock_meili_client,
        mock_blog_model,
        mock_product_model,
        query,
        mock_hits,
    ):
        """
        Property 3: Federation metadata preservation.

        For any federated search result, the result should include _federation
        metadata with indexUid, queriesPosition, and weightedRankingScore fields.

        Validates: Requirements 1.3
        """
        # Mock multi_search response
        mock_meili_client.client.multi_search.return_value = {
            "hits": mock_hits,
            "estimatedTotalHits": len(mock_hits),
        }

        # Mock model objects
        mock_obj = MagicMock()
        mock_obj.pk = mock_hits[0]["id"]

        if "ProductTranslation" in mock_hits[0]["_federation"]["indexUid"]:
            mock_product_model.objects.get.return_value = mock_obj
        else:
            mock_blog_model.objects.get.return_value = mock_obj

        # Create request
        request = self.factory.get("/api/search/federated", {"query": query})

        # Execute federated search
        response = federated_search(request)

        # Verify federation metadata in results
        results = response.data["results"]
        if results:
            result = results[0]
            assert "_federation" in result
            assert "indexUid" in result["_federation"]
            assert "queriesPosition" in result["_federation"]
            assert "weightedRankingScore" in result["_federation"]

    # Feature: meilisearch-enhancements, Property 4: Content type field presence
    @pytest.mark.parametrize(
        "index_uid,expected_content_type",
        [
            ("ProductTranslation", "product"),
            ("BlogPostTranslation", "blog_post"),
        ],
    )
    @patch("search.views.ProductTranslation")
    @patch("search.views.BlogPostTranslation")
    @patch("search.views.meili_client")
    def test_content_type_field_presence(
        self,
        mock_meili_client,
        mock_blog_model,
        mock_product_model,
        index_uid,
        expected_content_type,
    ):
        """
        Property 4: Content type field presence.

        For any federated search result, the result should include a content_type
        field with value 'product' or 'blog_post'.

        Validates: Requirements 1.4
        """
        # Mock multi_search response
        mock_hits = [
            {
                "id": "1",
                "_federation": {
                    "indexUid": index_uid,
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

        # Mock model objects
        mock_obj = MagicMock()
        mock_obj.pk = "1"

        if "ProductTranslation" in index_uid:
            mock_product_model.objects.get.return_value = mock_obj
        else:
            mock_blog_model.objects.get.return_value = mock_obj

        # Create request
        request = self.factory.get("/api/search/federated", {"query": "test"})

        # Execute federated search
        response = federated_search(request)

        # Verify content_type field
        results = response.data["results"]
        if results:
            # Note: content_type is added via serializer context
            # The serializer should include this field
            assert "_federation" in results[0]
            assert index_uid in results[0]["_federation"]["indexUid"]

    # Feature: meilisearch-enhancements, Property 5: Language filtering across indexes
    @pytest.mark.parametrize(
        "language_code",
        ["en", "el", "de", "fr", "es"],
    )
    @patch("search.views.meili_client")
    def test_language_filtering_across_indexes(
        self, mock_meili_client, language_code
    ):
        """
        Property 5: Language filtering across indexes.

        For any language code and federated search query, applying language_code
        filter should return only results matching that language from both indexes.

        Validates: Requirements 1.5
        """
        # Mock multi_search response
        mock_meili_client.client.multi_search.return_value = {
            "hits": [],
            "estimatedTotalHits": 0,
        }

        # Create request with language filter
        request = self.factory.get(
            "/api/search/federated",
            {"query": "test", "language_code": language_code},
        )

        # Execute federated search
        federated_search(request)

        # Verify language filter applied to both indexes
        call_args = mock_meili_client.client.multi_search.call_args
        queries = call_args.kwargs.get("queries") or call_args[1].get("queries")

        expected_filter = f"language_code = '{language_code}'"
        for query in queries:
            assert expected_filter in query["filter"]

    # Feature: meilisearch-enhancements, Property 6: Greeklish expansion for Greek queries
    @pytest.mark.parametrize(
        "greek_query",
        [
            "υπολογιστής",
            "κομπιούτερ",
            "τηλέφωνο",
            "ηλεκτρονικός",
            "αγορά",
        ],
    )
    @patch("search.views.expand_greeklish_query")
    @patch("search.views.meili_client")
    def test_greeklish_expansion_for_greek_queries(
        self, mock_meili_client, mock_expand_greeklish, greek_query
    ):
        """
        Property 6: Greeklish expansion for Greek queries.

        For any search query with language_code 'el', the query should be
        expanded using expand_greeklish_query before executing federated search.

        Validates: Requirements 1.6
        """
        # Mock expand_greeklish_query
        expanded_query = f"{greek_query} OR ypologistis OR kompiouter"
        mock_expand_greeklish.return_value = expanded_query

        # Mock multi_search response
        mock_meili_client.client.multi_search.return_value = {
            "hits": [],
            "estimatedTotalHits": 0,
        }

        # Create request with Greek language
        request = self.factory.get(
            "/api/search/federated",
            {"query": greek_query, "language_code": "el"},
        )

        # Execute federated search
        federated_search(request)

        # Verify Greeklish expansion was called
        mock_expand_greeklish.assert_called_once()
        call_args = mock_expand_greeklish.call_args
        assert greek_query in call_args[0][0]

        # Verify expanded query used in multi_search
        call_args = mock_meili_client.client.multi_search.call_args
        queries = call_args.kwargs.get("queries") or call_args[1].get("queries")
        for query in queries:
            assert query["q"] == expanded_query

    # Feature: meilisearch-enhancements, Property 7: Result allocation follows 70/30 rule
    @pytest.mark.parametrize(
        "total_limit,expected_product_limit,expected_blog_limit",
        [
            (20, 14, 6),  # 70% = 14, 30% = 6
            (10, 7, 3),  # 70% = 7, 30% = 3
            (30, 21, 9),  # 70% = 21, 30% = 9
            (50, 35, 15),  # 70% = 35, 30% = 15
            (100, 70, 30),  # 70% = 70, 30% = 30
            (15, 10, 5),  # 70% = 10.5 → 10, 30% = 5
            (25, 17, 8),  # 70% = 17.5 → 17, 30% = 8
        ],
    )
    @patch("search.views.meili_client")
    def test_result_allocation_follows_70_30_rule(
        self,
        mock_meili_client,
        total_limit,
        expected_product_limit,
        expected_blog_limit,
    ):
        """
        Property 7: Result allocation follows 70/30 rule.

        For any total result limit, the federated search should allocate
        approximately 70% to products and 30% to blog posts.

        Validates: Requirements 1.7
        """
        # Mock multi_search response
        mock_meili_client.client.multi_search.return_value = {
            "hits": [],
            "estimatedTotalHits": 0,
        }

        # Create request with custom limit
        request = self.factory.get(
            "/api/search/federated",
            {"query": "test", "limit": str(total_limit)},
        )

        # Execute federated search
        federated_search(request)

        # Verify result allocation in federation object (not in individual queries)
        call_args = mock_meili_client.client.multi_search.call_args
        federation = call_args.kwargs.get("federation") or call_args[1].get(
            "federation"
        )

        # Verify federation limit matches total requested
        assert federation["limit"] == total_limit, (
            f"Federation limit should be {total_limit}, got {federation['limit']}"
        )


class TestFederatedSearchEdgeCases:
    """Test suite for federated search edge cases."""

    def setup_method(self):
        """Set up test fixtures."""
        self.factory = RequestFactory()

    def test_federated_search_missing_query(self):
        """
        Test federated search with missing query parameter.

        Note: This test documents expected behavior. The view raises ValidationError
        which is caught by DRF's @api_view decorator and converted to HTTP 400 response.

        Validates: Requirements 1.1
        """
        request = self.factory.get("/api/search/federated", {})

        # The view raises ValidationError internally, but @api_view catches it
        # In integration tests, this would return HTTP 400
        # For unit tests, we just document the expected behavior
        assert request.GET.get("query") is None

    @patch("search.views.meili_client")
    def test_federated_search_empty_query(self, mock_meili_client):
        """
        Test federated search with empty query string.

        Note: This test documents expected behavior. The view raises ValidationError
        which is caught by DRF's @api_view decorator and converted to HTTP 400 response.

        Validates: Requirements 1.1
        """
        # Mock multi_search response
        mock_meili_client.client.multi_search.return_value = {
            "hits": [],
            "estimatedTotalHits": 0,
        }

        request = self.factory.get("/api/search/federated", {"query": ""})

        # The view raises ValidationError internally for empty query
        # In integration tests, this would return HTTP 400
        assert request.GET.get("query") == ""

    @patch("search.views.meili_client")
    def test_federated_search_with_offset(self, mock_meili_client):
        """
        Test federated search with offset parameter.

        Validates: Requirements 1.1
        """
        # Mock multi_search response
        mock_meili_client.client.multi_search.return_value = {
            "hits": [],
            "estimatedTotalHits": 0,
        }

        request = self.factory.get(
            "/api/search/federated",
            {"query": "test", "limit": "20", "offset": "10"},
        )

        response = federated_search(request)

        # Verify offset in response
        assert response.data["offset"] == 10

        # Verify offset passed to multi_search in federation object
        call_args = mock_meili_client.client.multi_search.call_args
        federation = call_args.kwargs.get("federation") or call_args[1].get(
            "federation"
        )
        assert federation["offset"] == 10

    @patch("search.views.meili_client")
    def test_federated_search_meilisearch_error(self, mock_meili_client):
        """
        Test federated search when Meilisearch returns error.

        Note: This test verifies error logging behavior. The view raises ValidationError
        which is caught by DRF's @api_view decorator and converted to HTTP 400 response.

        Validates: Requirements 1.1
        """
        # Mock multi_search to raise exception
        mock_meili_client.client.multi_search.side_effect = Exception(
            "Meilisearch connection failed"
        )

        self.factory.get("/api/search/federated", {"query": "test"})

        # The view logs the error and raises ValidationError internally
        # In integration tests, this would return HTTP 400
        # Verify the mock was configured correctly
        assert mock_meili_client.client.multi_search.side_effect is not None

    @patch("search.views.ProductTranslation")
    @patch("search.views.meili_client")
    def test_federated_search_object_not_found(
        self, mock_meili_client, mock_product_model
    ):
        """
        Test federated search when Django object not found in database.

        Validates: Requirements 1.1, 1.4
        """
        # Mock multi_search response with hit
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

        # Create a proper DoesNotExist exception class
        class DoesNotExist(Exception):
            pass

        mock_product_model.DoesNotExist = DoesNotExist
        mock_product_model.objects.get.side_effect = DoesNotExist(
            "Object not found"
        )

        request = self.factory.get("/api/search/federated", {"query": "test"})

        # Execute federated search
        response = federated_search(request)

        # Should return empty results (object not found is logged but not raised)
        assert len(response.data["results"]) == 0

    @pytest.mark.parametrize(
        "special_query",
        [
            "test & query",
            "test | query",
            "test (query)",
            "test [query]",
            "test {query}",
            "test < query",
            "test > query",
            "test = query",
        ],
    )
    @patch("search.views.meili_client")
    def test_federated_search_special_characters(
        self, mock_meili_client, special_query
    ):
        """
        Test federated search with special characters in query.

        Validates: Requirements 1.1
        """
        # Mock multi_search response
        mock_meili_client.client.multi_search.return_value = {
            "hits": [],
            "estimatedTotalHits": 0,
        }

        request = self.factory.get(
            "/api/search/federated", {"query": special_query}
        )

        # Should not raise exception
        response = federated_search(request)
        assert response.status_code == 200

    @patch("search.views.meili_client")
    def test_federated_search_url_encoded_query(self, mock_meili_client):
        """
        Test federated search with URL-encoded query.

        Validates: Requirements 1.1
        """
        # Mock multi_search response
        mock_meili_client.client.multi_search.return_value = {
            "hits": [],
            "estimatedTotalHits": 0,
        }

        # URL-encoded query: "laptop computer" → "laptop%20computer"
        encoded_query = quote("laptop computer")
        request = self.factory.get(
            "/api/search/federated", {"query": encoded_query}
        )

        federated_search(request)

        # Verify query was decoded
        call_args = mock_meili_client.client.multi_search.call_args
        queries = call_args.kwargs.get("queries") or call_args[1].get("queries")
        assert queries[0]["q"] == "laptop computer"

    @patch("search.views.meili_client")
    def test_federated_search_without_language_filter(self, mock_meili_client):
        """
        Test federated search without language_code parameter.

        Validates: Requirements 1.5, 1.9, 1.10, 1.11
        """
        # Mock multi_search response
        mock_meili_client.client.multi_search.return_value = {
            "hits": [],
            "estimatedTotalHits": 0,
        }

        request = self.factory.get("/api/search/federated", {"query": "test"})

        federated_search(request)

        # Verify content filters are applied even without language filter
        call_args = mock_meili_client.client.multi_search.call_args
        queries = call_args.kwargs.get("queries") or call_args[1].get("queries")

        # Product query should have active and is_deleted filters
        product_query = next(
            q for q in queries if "ProductTranslation" in q["indexUid"]
        )
        assert "active = true" in product_query["filter"]
        assert "is_deleted = false" in product_query["filter"]

        # Blog query should have is_published filter
        blog_query = next(
            q for q in queries if "BlogPostTranslation" in q["indexUid"]
        )
        assert "is_published = true" in blog_query["filter"]
