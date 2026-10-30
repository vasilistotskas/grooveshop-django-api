"""
Search analytics middleware for tracking search queries.

Records every successful (HTTP 200) response from a search-query
endpoint (``/api/v1/search/product``, ``/search/blog/post``) as a
``SearchQuery`` row, written by a Celery task so the response is never
blocked. Rows recorded as ``federated`` come from the federated
endpoint, removed 2026-10-25; the content type stays for their history.

Which requests count is decided by the ROUTE that served them, not by
the path: a ``/search/`` substring also matched the admin's command
palette (``/admin/search/``), recorded every keystroke as an empty
"federated" storefront search, and on the platform host sent the task
to a schema with no ``search_query`` table (2026-10-13).
"""

from __future__ import annotations

import ipaddress
import json
import logging
from collections.abc import Callable
from typing import cast
from uuid import uuid4

from django.http import HttpRequest, HttpResponseBase
from django.utils.deprecation import MiddlewareMixin

from core.client_ip import trusted_client_ip
from core.sessions import persisted_session_key

logger = logging.getLogger(__name__)


class SearchAnalyticsMiddleware(MiddlewareMixin):
    """
    Middleware to track search queries and results.

    Tracks the three query routes of ``search/urls.py`` and only their
    HTTP 200 responses. Trending, analytics and click are routes of the
    same app but not searches.

    - Extracts the query parameters and the results metadata
    - Dispatches a Celery task to persist the record
    - Logs, never raises, when tracking fails
    - Captures the user, session key, IP and user agent
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponseBase]):
        """Initialize middleware with response handler."""
        self.get_response = get_response
        super().__init__(get_response)

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        """Process request and track search analytics."""
        # Get response first
        response = cast(HttpResponseBase, self.get_response(request))

        route = self._query_route(request)
        if route is not None:
            self._track_search_async(
                request, response, self._QUERY_ROUTES[route]
            )

        return response

    # The query routes of ``search/urls.py`` and the content type each
    # one records.
    _QUERY_ROUTES = {
        "search-product": "product",
        "search-blog-post": "blog_post",
    }

    def _query_route(self, request: HttpRequest) -> str | None:
        """The query route that served this request, if any.

        ``resolver_match`` is set once the URL is resolved, so it is
        there after ``get_response``. Unresolved requests have none.
        """
        match = getattr(request, "resolver_match", None)
        if match is None or match.view_name not in self._QUERY_ROUTES:
            return None
        return match.view_name

    def _track_search_async(
        self,
        request: HttpRequest,
        response: HttpResponseBase,
        content_type: str,
    ) -> None:
        """
        Track search query without blocking the response.

        Dispatches a Celery task with ``.delay()`` so the DB write is
        decoupled from the request/response cycle.

        Args:
            request: The HTTP request
            response: The HTTP response
        """
        try:
            # Only track successful responses
            if response.status_code != 200:
                return

            # A facet read (see ``search.views._mark_count_only``) is not
            # a search the shopper made.
            if getattr(request, "search_is_count_only", False):
                return

            # Extract query parameters
            query = request.GET.get("query", "")

            # A blank query is a listing load, not a search: the
            # storefront's category and listing pages read this same
            # endpoint with no text, as does its cache warm-up. Every
            # reader of the analytics already leaves these rows out, and
            # they were 96% of the table.
            if not query.strip():
                return
            language_code = request.GET.get("language_code", "")

            # Parse response data to extract results metadata
            results_count = 0
            estimated_total_hits = 0
            processing_time_ms = None
            query_uuid = None

            try:
                # Try to parse JSON response
                if hasattr(response, "content"):
                    response_data = json.loads(response.content.decode("utf-8"))
                    results_count = len(response_data.get("results", []))
                    # Handle both snake_case and camelCase
                    estimated_total_hits = response_data.get(
                        "estimatedTotalHits"
                    ) or response_data.get("estimated_total_hits", 0)
                    # Body fallback only. No search endpoint puts the
                    # engine's timing in its response — deliberately, it
                    # is not part of the public contract — so in practice
                    # the value comes from the request attribute below.
                    # Kept because it costs nothing and a future endpoint
                    # may expose it.
                    # ``is not None`` rather than ``or``: 0 ms is a real
                    # reading from a small index, and an ``or`` chain
                    # would discard it as falsy.
                    body_processing_time_ms = response_data.get(
                        "processingTimeMs"
                    )
                    if body_processing_time_ms is None:
                        body_processing_time_ms = response_data.get(
                            "processing_time_ms"
                        )
                    if body_processing_time_ms is not None:
                        processing_time_ms = body_processing_time_ms
                    # The view mints a query_id per search response; the
                    # stored row carries it so a later click submitted
                    # with that id can be attributed to this query.
                    query_uuid = response_data.get(
                        "queryId"
                    ) or response_data.get("query_id")
            except (json.JSONDecodeError, AttributeError, KeyError) as e:
                logger.debug(f"Could not parse response data: {e!s}")
            # Every stored search has a key, minted here when the
            # response carried none: the task's retry is keyed on it.
            query_uuid = query_uuid or str(uuid4())

            # The view stamps Meilisearch's own processing time here (see
            # ``search.views._record_engine_time``), summed over every
            # engine call the request made. It wins over the body: it is
            # the real measurement, while the body has never carried one.
            engine_ms = getattr(request, "search_processing_time_ms", None)
            if engine_ms is not None:
                processing_time_ms = engine_ms

            # Extract user information
            user = request.user if request.user.is_authenticated else None
            # Only a session that exists: an anonymous search never
            # loads its session, so ``session_key`` would be whatever
            # cookie the client sent. No session is spelled "", like
            # every other absent string on the row.
            session_key = persisted_session_key(request)
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
                query_uuid=query_uuid,
            )

            logger.debug(
                "Tracked search query: '%s' (%s) - %s results",
                query,
                content_type,
                results_count,
            )

        except Exception:
            # Log error but don't break the request
            logger.exception(
                "Failed to track search analytics",
                extra={
                    "path": request.path,
                    "query": request.GET.get("query", ""),
                },
            )

    def _get_client_ip(self, request: HttpRequest) -> str | None:
        """
        Extract client IP address from request.

        Prefers ``trusted_client_ip`` (the visitor Cloudflare vouched
        for). Otherwise: the app runs behind a single trusted reverse
        proxy (Traefik in K8s). REMOTE_ADDR is the proxy address (a
        private/loopback IP), so we use the *rightmost* entry in
        X-Forwarded-For — the one appended by our trusted proxy — rather
        than the leftmost, which an attacker can spoof. If REMOTE_ADDR is
        not a private IP we trust it directly.

        Args:
            request: The HTTP request

        Returns:
            Client IP address or None if not available
        """
        # Preferred: the visitor Cloudflare reported, accepted only when
        # the request provably came through our edge. Without this the
        # value below is Traefik's TCP peer — the Cloudflare edge node, or
        # (before externalTrafficPolicy: Local) the internal 10.42.x.x SNAT
        # address — so rows share it and the field says little about who
        # searched.
        edge_ip = trusted_client_ip(request)
        if edge_ip:
            return edge_ip

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
