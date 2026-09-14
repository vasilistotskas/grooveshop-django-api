"""Meilisearch's processing time must reach the analytics row.

Before this, `meili/querysets.py` dropped `processingTimeMs` and no search
endpoint emitted it, so every `SearchQuery` stored NULL and the analytics
endpoint's `performance.avgProcessingTimeMs` was pinned at 0.0 — a number
that looked like a measurement and was not one.

The value is handed from view to middleware on the request rather than
through the response body, so it stays out of the public OpenAPI contract.
That makes the DRF wrapper the sharp edge these tests guard.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from django.test import RequestFactory
from rest_framework.request import Request

from search.views import _record_engine_time


@pytest.fixture
def http_request():
    return RequestFactory().get("/api/v1/search/product?query=x")


class TestRecordEngineTime:
    def test_records_a_value(self, http_request):
        _record_engine_time(http_request, 12)
        assert http_request.search_processing_time_ms == 12

    def test_accumulates_across_calls(self, http_request):
        """A relaxed-query retry is a second engine call, not a replacement."""
        _record_engine_time(http_request, 12)
        _record_engine_time(http_request, 7)
        assert http_request.search_processing_time_ms == 19

    def test_zero_is_a_measurement_not_an_absence(self, http_request):
        """Meilisearch reports 0 ms routinely on a small index."""
        _record_engine_time(http_request, 0)
        assert http_request.search_processing_time_ms == 0

    def test_none_is_skipped(self, http_request):
        _record_engine_time(http_request, None)
        assert not hasattr(http_request, "search_processing_time_ms")

    def test_garbage_is_skipped_without_raising(self, http_request):
        """A malformed engine payload must never break the search response."""
        _record_engine_time(http_request, "not-a-number")
        assert not hasattr(http_request, "search_processing_time_ms")


class TestReachesTheUnderlyingRequest:
    """The trap: DRF's Request proxies reads, not writes.

    `rest_framework.request.Request` defines `__getattr__` but not
    `__setattr__`, so stamping the wrapper stores the value on the wrapper
    and middleware — handed the plain HttpRequest — reads nothing. The
    write succeeds, so nothing surfaces the mistake.
    """

    def test_stamping_a_drf_request_reaches_the_http_request(
        self, http_request
    ):
        drf_request = Request(http_request)

        _record_engine_time(drf_request, 15)

        # What the middleware actually holds:
        assert http_request.search_processing_time_ms == 15

    def test_accumulates_across_the_wrapper(self, http_request):
        drf_request = Request(http_request)

        _record_engine_time(drf_request, 15)
        _record_engine_time(drf_request, 5)

        assert http_request.search_processing_time_ms == 20


class TestMiddlewarePrefersTheRequestAttribute:
    def test_request_attribute_wins_over_an_absent_body_value(
        self, http_request
    ):
        from search.middleware import SearchAnalyticsMiddleware

        http_request.search_processing_time_ms = 21
        http_request.user = MagicMock(is_authenticated=False)

        response = MagicMock()
        response.status_code = 200
        response.content = b'{"results": [], "estimatedTotalHits": 0}'

        captured = {}
        middleware = SearchAnalyticsMiddleware(lambda r: response)

        import search.middleware as mw

        original = mw.__dict__.get("save_search_query")
        try:
            import search.tasks

            def _capture(**kwargs):
                captured.update(kwargs)

            search.tasks.save_search_query.delay = _capture
            middleware._track_search_async(http_request, response)
        finally:
            if original is not None:  # pragma: no cover - defensive
                mw.save_search_query = original

        assert captured["processing_time_ms"] == 21
