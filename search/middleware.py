"""
Search analytics middleware for tracking search queries.

This middleware automatically tracks search queries to /api/search/* endpoints
and creates SearchQuery records asynchronously without blocking the response.
"""

from __future__ import annotations

import json
import logging
from typing import Callable

from django.http import HttpRequest, HttpResponse
from django.utils.deprecation import MiddlewareMixin

from search.models import SearchQuery

logger = logging.getLogger(__name__)


class SearchAnalyticsMiddleware(MiddlewareMixin):
    """
    Middleware to track search queries and results.

    This middleware intercepts requests to /api/search/* endpoints and
    creates SearchQuery records with metadata about the query and results.

    Features:
    - Tracks all search endpoints (product, blog, federated)
    - Extracts query parameters and results metadata
    - Creates SearchQuery records asynchronously
    - Handles failures gracefully (logs but doesn't break requests)
    - Captures user information (authenticated user, session, IP, user agent)

    Requirements: 2.1, 2.2, 2.7, 2.8
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]):
        """Initialize middleware with response handler."""
        self.get_response = get_response
        super().__init__(get_response)

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Process request and track search analytics."""
        # Get response first
        response = self.get_response(request)

        # Track search if this is a search endpoint
        if self._is_search_endpoint(request):
            self._track_search_async(request, response)

        return response

    def _is_search_endpoint(self, request: HttpRequest) -> bool:
        """
        Check if the request is to a search endpoint.

        Args:
            request: The HTTP request

        Returns:
            True if the request path contains /search/
        """
        return "/search/" in request.path

    def _track_search_async(
        self, request: HttpRequest, response: HttpResponse
    ) -> None:
        """
        Track search query asynchronously.

        This method extracts query parameters and results metadata from
        the request and response, then creates a SearchQuery record.
        Failures are logged but don't affect the response.

        Args:
            request: The HTTP request
            response: The HTTP response
        """
        try:
            # Only track successful responses
            if response.status_code != 200:
                return

            # Extract query parameters
            query = request.GET.get("query", "")
            language_code = request.GET.get("language_code")

            # Determine content type from endpoint path
            content_type = self._determine_content_type(request.path)

            # Parse response data to extract results metadata
            results_count = 0
            estimated_total_hits = 0
            processing_time_ms = None

            try:
                # Try to parse JSON response
                if hasattr(response, "content"):
                    response_data = json.loads(response.content.decode("utf-8"))
                    results_count = len(response_data.get("results", []))
                    # Handle both snake_case and camelCase (DRF middleware converts to camelCase)
                    estimated_total_hits = response_data.get(
                        "estimatedTotalHits"
                    ) or response_data.get("estimated_total_hits", 0)
                    processing_time_ms = response_data.get(
                        "processingTimeMs"
                    ) or response_data.get("processing_time_ms")
            except (json.JSONDecodeError, AttributeError, KeyError) as e:
                logger.debug(f"Could not parse response data: {str(e)}")

            # Extract user information
            user = request.user if request.user.is_authenticated else None
            session_key = (
                request.session.session_key
                if hasattr(request, "session")
                else None
            )
            ip_address = self._get_client_ip(request)
            user_agent = request.META.get("HTTP_USER_AGENT", "")

            # Create SearchQuery record
            SearchQuery.objects.create(
                query=query,
                language_code=language_code,
                content_type=content_type,
                results_count=results_count,
                estimated_total_hits=estimated_total_hits,
                processing_time_ms=processing_time_ms,
                user=user,
                session_key=session_key,
                ip_address=ip_address,
                user_agent=user_agent,
            )

            logger.debug(
                f"Tracked search query: '{query}' ({content_type}) - "
                f"{results_count} results"
            )

        except Exception as e:
            # Log error but don't break the request
            logger.error(
                f"Failed to track search analytics: {str(e)}",
                exc_info=True,
                extra={
                    "path": request.path,
                    "query": request.GET.get("query", ""),
                },
            )

    def _determine_content_type(self, path: str) -> str:
        """
        Determine content type from request path.

        Args:
            path: The request path

        Returns:
            Content type string ('product', 'blog_post', or 'federated')
        """
        if "product" in path:
            return "product"
        elif "blog" in path:
            return "blog_post"
        elif "federated" in path:
            return "federated"
        else:
            # Default to federated for unknown search endpoints
            return "federated"

    def _get_client_ip(self, request: HttpRequest) -> str | None:
        """
        Extract client IP address from request.

        Handles proxy headers (X-Forwarded-For) for accurate IP detection.

        Args:
            request: The HTTP request

        Returns:
            Client IP address or None if not available
        """
        # Check for proxy headers first
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded_for:
            # X-Forwarded-For can contain multiple IPs, take the first one
            ip = x_forwarded_for.split(",")[0].strip()
            return ip

        # Fall back to REMOTE_ADDR
        return request.META.get("REMOTE_ADDR")
