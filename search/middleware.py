"""
Search analytics middleware for tracking search queries.

This middleware automatically tracks search queries to /api/search/* endpoints
and creates SearchQuery records without blocking the response.
"""

from __future__ import annotations

import ipaddress
import json
import logging
from typing import Callable, cast

from django.http import HttpRequest, HttpResponseBase
from django.utils.deprecation import MiddlewareMixin

logger = logging.getLogger(__name__)


class SearchAnalyticsMiddleware(MiddlewareMixin):
    """
    Middleware to track search queries and results.

    This middleware intercepts requests to /api/search/* endpoints and
    creates SearchQuery records with metadata about the query and results.

    Features:
    - Tracks all search endpoints (product, blog, federated)
    - Extracts query parameters and results metadata
    - Dispatches a Celery task to persist the record without blocking the response
    - Handles failures gracefully (logs but doesn't break requests)
    - Captures user information (authenticated user, session, IP, user agent)

    Requirements: 2.1, 2.2, 2.7, 2.8
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponseBase]):
        """Initialize middleware with response handler."""
        self.get_response = get_response
        super().__init__(get_response)

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        """Process request and track search analytics."""
        # Get response first
        response = cast(HttpResponseBase, self.get_response(request))

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
        self, request: HttpRequest, response: HttpResponseBase
    ) -> None:
        """
        Track search query without blocking the response.

        Dispatches a Celery task via on_commit so the DB write is fully
        decoupled from the request/response cycle.

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
                    # Handle both snake_case and camelCase
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

            # Celery .delay() is the fire-and-forget primitive here —
            # no transaction.on_commit() wrapper needed (and it would be
            # a no-op in autocommit mode anyway).
            from search.tasks import save_search_query

            save_search_query.delay(
                query=query,
                language_code=language_code,
                content_type=content_type,
                results_count=results_count,
                estimated_total_hits=estimated_total_hits,
                processing_time_ms=processing_time_ms,
                user_id=user.pk if user else None,
                session_key=session_key,
                ip_address=ip_address,
                user_agent=user_agent,
            )

            logger.debug(
                "Tracked search query: '%s' (%s) - %s results",
                query,
                content_type,
                results_count,
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

        The app runs behind a single trusted reverse proxy (Traefik in K8s).
        REMOTE_ADDR is the proxy address (a private/loopback IP), so we use
        the *rightmost* entry in X-Forwarded-For — the one appended by our
        trusted proxy — rather than the leftmost, which an attacker can spoof.
        If REMOTE_ADDR is not a private IP we trust it directly.

        Args:
            request: The HTTP request

        Returns:
            Client IP address or None if not available
        """
        remote_addr = request.META.get("REMOTE_ADDR", "")

        try:
            addr = ipaddress.ip_address(remote_addr)
            behind_proxy = (
                addr.is_loopback or addr.is_private or addr.is_link_local
            )
        except ValueError:
            behind_proxy = False

        if behind_proxy:
            xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
            entries = [e.strip() for e in xff.split(",") if e.strip()]
            return entries[-1] if entries else remote_addr or None

        return remote_addr or None
