"""
Integration tests for SearchAnalyticsMiddleware.

Tests the middleware's ability to track search queries and handle
various scenarios including successful searches, failures, and edge cases.
"""

from __future__ import annotations

import json
from unittest.mock import Mock, patch

import pytest
from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.test import RequestFactory

from search.middleware import SearchAnalyticsMiddleware
from search.models import SearchQuery

User = get_user_model()


@pytest.fixture
def request_factory():
    """Provide Django RequestFactory for creating test requests."""
    return RequestFactory()


@pytest.fixture
def middleware():
    """Provide SearchAnalyticsMiddleware instance."""

    def get_response(request):
        # Mock response with search results
        response_data = {
            "results": [{"id": 1}, {"id": 2}],
            "estimated_total_hits": 10,
            "processing_time_ms": 50,
        }
        response = HttpResponse(
            json.dumps(response_data),
            content_type="application/json",
            status=200,
        )
        return response

    return SearchAnalyticsMiddleware(get_response)


@pytest.fixture
def authenticated_user(db):
    """Create an authenticated user for testing."""
    return User.objects.create_user(
        username="testuser", email="test@example.com", password="testpass123"
    )


@pytest.mark.django_db
class TestSearchAnalyticsMiddleware:
    """Test suite for SearchAnalyticsMiddleware."""

    def test_tracks_product_search(self, request_factory, middleware):
        """Test that product search queries are tracked."""
        # Create request to product search endpoint
        request = request_factory.get(
            "/api/search/product", {"query": "laptop", "language_code": "en"}
        )
        request.user = Mock(is_authenticated=False)
        request.session = Mock(session_key="test-session-123")

        # Process request through middleware
        initial_count = SearchQuery.objects.count()
        middleware(request)

        # Verify SearchQuery was created
        assert SearchQuery.objects.count() == initial_count + 1

        # Verify query details
        search_query = SearchQuery.objects.latest("timestamp")
        assert search_query.query == "laptop"
        assert search_query.language_code == "en"
        assert search_query.content_type == "product"
        assert search_query.results_count == 2
        assert search_query.estimated_total_hits == 10
        assert search_query.processing_time_ms == 50

    def test_tracks_blog_search(self, request_factory, middleware):
        """Test that blog post search queries are tracked."""
        request = request_factory.get(
            "/api/search/blog", {"query": "article", "language_code": "el"}
        )
        request.user = Mock(is_authenticated=False)
        request.session = Mock(session_key="test-session-456")

        initial_count = SearchQuery.objects.count()
        middleware(request)

        assert SearchQuery.objects.count() == initial_count + 1

        search_query = SearchQuery.objects.latest("timestamp")
        assert search_query.query == "article"
        assert search_query.language_code == "el"
        assert search_query.content_type == "blog_post"

    def test_tracks_federated_search(self, request_factory, middleware):
        """Test that federated search queries are tracked."""
        request = request_factory.get(
            "/api/search/federated",
            {"query": "test query", "language_code": "de"},
        )
        request.user = Mock(is_authenticated=False)
        request.session = Mock(session_key="test-session-789")

        initial_count = SearchQuery.objects.count()
        middleware(request)

        assert SearchQuery.objects.count() == initial_count + 1

        search_query = SearchQuery.objects.latest("timestamp")
        assert search_query.query == "test query"
        assert search_query.language_code == "de"
        assert search_query.content_type == "federated"

    def test_tracks_authenticated_user(
        self, request_factory, middleware, authenticated_user
    ):
        """Test that authenticated user information is captured."""
        request = request_factory.get(
            "/api/search/product", {"query": "laptop"}
        )
        request.user = authenticated_user
        request.session = Mock(session_key="auth-session-123")

        middleware(request)

        search_query = SearchQuery.objects.latest("timestamp")
        assert search_query.user == authenticated_user
        assert search_query.session_key == "auth-session-123"

    def test_captures_ip_address(self, request_factory, middleware):
        """Test that client IP address is captured."""
        request = request_factory.get(
            "/api/search/product", {"query": "laptop"}
        )
        request.user = Mock(is_authenticated=False)
        request.session = Mock(session_key="test-session")
        request.META["REMOTE_ADDR"] = "192.168.1.100"

        middleware(request)

        search_query = SearchQuery.objects.latest("timestamp")
        assert search_query.ip_address == "192.168.1.100"

    def test_captures_ip_from_proxy_header(self, request_factory, middleware):
        """Test that IP is extracted from X-Forwarded-For header."""
        request = request_factory.get(
            "/api/search/product", {"query": "laptop"}
        )
        request.user = Mock(is_authenticated=False)
        request.session = Mock(session_key="test-session")
        request.META["HTTP_X_FORWARDED_FOR"] = "203.0.113.1, 198.51.100.1"
        request.META["REMOTE_ADDR"] = "192.168.1.100"

        middleware(request)

        search_query = SearchQuery.objects.latest("timestamp")
        # Should use first IP from X-Forwarded-For
        assert search_query.ip_address == "203.0.113.1"

    def test_captures_user_agent(self, request_factory, middleware):
        """Test that user agent string is captured."""
        request = request_factory.get(
            "/api/search/product", {"query": "laptop"}
        )
        request.user = Mock(is_authenticated=False)
        request.session = Mock(session_key="test-session")
        request.META["HTTP_USER_AGENT"] = "Mozilla/5.0 Test Browser"

        middleware(request)

        search_query = SearchQuery.objects.latest("timestamp")
        assert search_query.user_agent == "Mozilla/5.0 Test Browser"

    def test_handles_empty_query(self, request_factory, middleware):
        """Test that empty queries are tracked."""
        request = request_factory.get(
            "/api/search/product", {"query": "", "language_code": "en"}
        )
        request.user = Mock(is_authenticated=False)
        request.session = Mock(session_key="test-session")

        initial_count = SearchQuery.objects.count()
        middleware(request)

        assert SearchQuery.objects.count() == initial_count + 1

        search_query = SearchQuery.objects.latest("timestamp")
        assert search_query.query == ""

    def test_handles_missing_language_code(self, request_factory, middleware):
        """Test that queries without language_code are tracked."""
        request = request_factory.get(
            "/api/search/product", {"query": "laptop"}
        )
        request.user = Mock(is_authenticated=False)
        request.session = Mock(session_key="test-session")

        middleware(request)

        search_query = SearchQuery.objects.latest("timestamp")
        assert search_query.query == "laptop"
        assert search_query.language_code is None

    def test_ignores_non_search_endpoints(self, request_factory):
        """Test that non-search endpoints are not tracked."""

        def get_response(request):
            return HttpResponse("OK", status=200)

        middleware = SearchAnalyticsMiddleware(get_response)

        request = request_factory.get("/api/products/")
        request.user = Mock(is_authenticated=False)

        initial_count = SearchQuery.objects.count()
        middleware(request)

        # No SearchQuery should be created
        assert SearchQuery.objects.count() == initial_count

    def test_ignores_non_200_responses(self, request_factory):
        """Test that failed requests (non-200) are not tracked."""

        def get_response(request):
            return HttpResponse("Error", status=400)

        middleware = SearchAnalyticsMiddleware(get_response)

        request = request_factory.get(
            "/api/search/product", {"query": "laptop"}
        )
        request.user = Mock(is_authenticated=False)

        initial_count = SearchQuery.objects.count()
        middleware(request)

        # No SearchQuery should be created for failed requests
        assert SearchQuery.objects.count() == initial_count

    def test_handles_database_error_gracefully(self, request_factory):
        """
        Test that database errors don't break the request.

        Feature: meilisearch-enhancements, Property 11: Analytics logging
        failures don't break search

        Validates: Requirements 2.7
        """

        def get_response(request):
            response_data = {"results": [], "estimated_total_hits": 0}
            return HttpResponse(
                json.dumps(response_data),
                content_type="application/json",
                status=200,
            )

        middleware = SearchAnalyticsMiddleware(get_response)

        request = request_factory.get(
            "/api/search/product", {"query": "laptop"}
        )
        request.user = Mock(is_authenticated=False)
        request.session = Mock(session_key="test-session")

        # Mock SearchQuery.objects.create to raise an exception
        with patch.object(
            SearchQuery.objects,
            "create",
            side_effect=Exception("Database error"),
        ):
            # Request should still succeed
            response = middleware(request)
            assert response.status_code == 200

    def test_handles_invalid_json_response(self, request_factory):
        """Test that invalid JSON responses are handled gracefully."""

        def get_response(request):
            return HttpResponse("Invalid JSON", status=200)

        middleware = SearchAnalyticsMiddleware(get_response)

        request = request_factory.get(
            "/api/search/product", {"query": "laptop"}
        )
        request.user = Mock(is_authenticated=False)
        request.session = Mock(session_key="test-session")

        # Should not raise an exception
        response = middleware(request)
        assert response.status_code == 200

        # SearchQuery should still be created with default values
        search_query = SearchQuery.objects.latest("timestamp")
        assert search_query.query == "laptop"
        assert search_query.results_count == 0
        assert search_query.estimated_total_hits == 0

    def test_handles_special_characters_in_query(
        self, request_factory, middleware
    ):
        """Test that queries with special characters are tracked correctly."""
        special_query = "laptop!@#$%^&*()_+-=[]{}|;:,.<>?"

        request = request_factory.get(
            "/api/search/product", {"query": special_query}
        )
        request.user = Mock(is_authenticated=False)
        request.session = Mock(session_key="test-session")

        middleware(request)

        search_query = SearchQuery.objects.latest("timestamp")
        assert search_query.query == special_query

    def test_handles_unicode_query(self, request_factory, middleware):
        """Test that Unicode queries are tracked correctly."""
        unicode_query = "υπολογιστής café 日本語"

        request = request_factory.get(
            "/api/search/product", {"query": unicode_query}
        )
        request.user = Mock(is_authenticated=False)
        request.session = Mock(session_key="test-session")

        middleware(request)

        search_query = SearchQuery.objects.latest("timestamp")
        assert search_query.query == unicode_query

    def test_handles_long_query(self, request_factory, middleware):
        """Test that long queries are tracked (up to 500 chars)."""
        long_query = "a" * 500

        request = request_factory.get(
            "/api/search/product", {"query": long_query}
        )
        request.user = Mock(is_authenticated=False)
        request.session = Mock(session_key="test-session")

        middleware(request)

        search_query = SearchQuery.objects.latest("timestamp")
        assert search_query.query == long_query
        assert len(search_query.query) == 500


@pytest.mark.django_db
class TestSearchAnalyticsMiddlewareEdgeCases:
    """Test edge cases and error conditions."""

    def test_handles_missing_session(self, request_factory):
        """Test that requests without session are handled."""

        def get_response(request):
            response_data = {"results": [], "estimated_total_hits": 0}
            return HttpResponse(
                json.dumps(response_data),
                content_type="application/json",
                status=200,
            )

        middleware = SearchAnalyticsMiddleware(get_response)

        request = request_factory.get(
            "/api/search/product", {"query": "laptop"}
        )
        request.user = Mock(is_authenticated=False)
        # No session attribute

        middleware(request)

        search_query = SearchQuery.objects.latest("timestamp")
        assert search_query.session_key is None

    def test_handles_missing_user_agent(self, request_factory):
        """Test that requests without user agent are handled."""

        def get_response(request):
            response_data = {"results": [], "estimated_total_hits": 0}
            return HttpResponse(
                json.dumps(response_data),
                content_type="application/json",
                status=200,
            )

        middleware = SearchAnalyticsMiddleware(get_response)

        request = request_factory.get(
            "/api/search/product", {"query": "laptop"}
        )
        request.user = Mock(is_authenticated=False)
        request.session = Mock(session_key="test-session")
        # No HTTP_USER_AGENT in META

        middleware(request)

        search_query = SearchQuery.objects.latest("timestamp")
        assert search_query.user_agent == ""

    def test_determines_content_type_for_unknown_endpoint(
        self, request_factory
    ):
        """Test that unknown search endpoints default to 'federated'."""

        def get_response(request):
            response_data = {"results": [], "estimated_total_hits": 0}
            return HttpResponse(
                json.dumps(response_data),
                content_type="application/json",
                status=200,
            )

        middleware = SearchAnalyticsMiddleware(get_response)

        request = request_factory.get("/api/search/unknown", {"query": "test"})
        request.user = Mock(is_authenticated=False)
        request.session = Mock(session_key="test-session")

        middleware(request)

        search_query = SearchQuery.objects.latest("timestamp")
        assert search_query.content_type == "federated"
