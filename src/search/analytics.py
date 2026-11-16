"""What a search request tells ``SearchAnalyticsMiddleware``.

The views stamp the underlying ``HttpRequest``; the middleware reads it
back after the response. Nothing here goes into a response body.
"""

from __future__ import annotations

# A request for at most this many products that asks for facets reads
# counts, not results (see ``mark_count_only``).
COUNT_ONLY_MAX_LIMIT = 1


def record_engine_time(request, milliseconds) -> None:
    """Accumulate Meilisearch's own processing time for this request.

    Read back by ``SearchAnalyticsMiddleware`` and stored on
    ``SearchQuery.processing_time_ms``. Handing it over on the request
    rather than through the response body keeps it out of the public
    OpenAPI contract: no client needs the engine's timing, and coupling
    analytics to the response shape would mean the field could never be
    removed again.

    ACCUMULATED, not assigned. A query that returns nothing is retried
    once with its leading word dropped (``relaxed_query``), so the
    honest answer to "how long did the engine spend on this request" is
    every pass, not the last one.

    Meilisearch reports whole milliseconds and commonly reports 0 for a
    small index, so a falsy value is still a real measurement — only
    ``None`` (the field absent) is skipped.

    Written to the UNDERLYING ``HttpRequest``. DRF's ``Request`` proxies
    attribute *reads* to it via ``__getattr__`` but does not override
    ``__setattr__``, so stamping the wrapper would leave the value where
    middleware — which is handed the plain ``HttpRequest`` — can never
    see it. That failure is silent: the write succeeds and the read
    returns nothing.
    """
    if milliseconds is None:
        return
    try:
        total = int(milliseconds)
    except TypeError, ValueError:
        return
    target = getattr(request, "_request", request)
    target.search_processing_time_ms = (
        getattr(target, "search_processing_time_ms", 0) or 0
    ) + total


def mark_count_only(request) -> None:
    """Say that this request is a facet read, not a customer's search.

    The sidebar's counts and ranges used to be asked for as searches of
    their own: ``facets=...`` with ``limit`` 0 or 1, because the response
    needs a page of results and the caller wants none. Such a request
    shows the shopper nothing to click, so ``SearchAnalyticsMiddleware``
    must not store it as a query they made, or one page view would count
    as several searches (and a facet read repeating the shopper's text
    would double that search). The rule is "asks for facets and for at
    most ``COUNT_ONLY_MAX_LIMIT`` product"; a request for results, with
    or without facets, is a search.

    Written to the underlying ``HttpRequest`` for the reason given on
    ``record_engine_time``.
    """
    target = getattr(request, "_request", request)
    target.search_is_count_only = True
