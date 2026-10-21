"""One access line per request, with its latency and error code.

Gunicorn's access log carries no duration and the ``django`` logger only
speaks at WARNING, so the one per-request latency Django exposed was
Traefik's per-service histogram — nothing a slow storefront request could
be followed into. Each line carries the ``correlation_id`` the storefront
sends as ``X-Correlation-ID``, which ``CorrelationIdMiddleware`` binds and
the handler's ``CorrelationIdFilter`` stamps on every record.

Where it sits in ``MIDDLEWARE`` is part of the contract:

* AFTER ``HealthProbeMiddleware``: kubelet's readiness probe is answered
  there, every 15 s per pod, and never reaches this layer. The liveness
  probe (``/health/``) is answered even earlier, at the ASGI layer. Probe
  traffic is therefore absent without a path list to keep in sync; the
  startup probe (``/api/v1/health``) is a real request that runs only
  while a pod boots, and is logged like one.
* AFTER ``CorrelationIdMiddleware``: its ContextVar is reset when it
  returns, so a layer outside it would log ``correlation_id="-"``.
* BEFORE ``TenantMainMiddleware``: a host no ``TenantDomain`` matches
  (404), a suspended store and a CORS preflight are answered below it,
  so only a layer above it sees them; the duration also includes
  tenant resolution.

An error response also names its ``code``, the stable identifier of the
API error contract (``docs/error-contract.md``), so a spike of one code
(checkout's ``payment_intent_failed``, a 5xx) can be counted and alerted
on per route. It is read here, from the response, rather than in the
exception handler, because the handler only sees RAISED errors: the
order, cart and payment views build most of their error responses
themselves (``return Response({..., "code": ...}, status=4xx)``) and
those never reach it. This layer sees both, and logs each response once.
A 5xx line is a WARNING, everything else INFO; ``django.request`` still
logs the 5xx at ERROR, with the traceback when there is one, so the
traceback is never repeated here.

Never logged: the raw path (it carries ids), the query string, headers,
cookies, bodies, error messages. The route is the URL PATTERN the request
resolved to; the code is a fixed identifier, never text a client sent.
"""

from __future__ import annotations

import logging
import time

from django.urls import Resolver404, resolve
from rest_framework import status

from core.api.exception_handler import CODE_KEY

logger = logging.getLogger(__name__)

#: ``route`` of a request no URL pattern matched. A fixed label rather
#: than the raw path, so a scanner walking random URLs cannot mint a new
#: value per request.
UNMATCHED_ROUTE = "unmatched"


def _route_of(request) -> str:
    """The URL pattern the request belongs to, or ``UNMATCHED_ROUTE``.

    ``resolver_match`` is set only when a view ran. A middleware that
    answers on its own — the internal domains feed, a CORS preflight —
    leaves it empty, so the path is resolved here against the urlconf
    the request was routed with; only a path no pattern matches stays
    unmatched.
    """
    match = request.resolver_match
    if match is None:
        try:
            match = resolve(
                request.path_info, getattr(request, "urlconf", None)
            )
        except Resolver404:
            return UNMATCHED_ROUTE
    return match.route or "/"


def _code_of(response) -> str | None:
    """The error ``code`` of an API error response, else ``None``.

    Only a DRF ``Response`` carries ``data``; Django's own error pages
    (an unhandled exception's 500, an unknown host's 404) have no code.
    A validation body with a FIELD named ``code`` (a coupon code) holds a
    list there and no top-level code, so only a string counts.
    """
    if response.status_code < status.HTTP_400_BAD_REQUEST:
        return None
    data = getattr(response, "data", None)
    if not isinstance(data, dict):
        return None
    code = data.get(CODE_KEY)
    return code if isinstance(code, str) else None


class AccessLogMiddleware:
    """Log ``method route status duration_ms [code]`` once per request.

    Sync-only like every other middleware here; Django adapts it for the
    ASGI stack and both sync and async views. The duration runs until
    the response object comes back, so the time to stream a
    ``StreamingHttpResponse`` body is not included. An exception a view
    raises has already been converted to a 500 response by the layer
    below, so it is logged with that status.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        started = time.perf_counter()
        response = self.get_response(request)
        duration_ms = round((time.perf_counter() - started) * 1000, 1)

        route = _route_of(request)
        fields: dict[str, object] = {
            "method": request.method,
            "route": route,
            "status": response.status_code,
            "duration_ms": duration_ms,
        }
        code = _code_of(response)
        if code is not None:
            fields["code"] = code
        logger.log(
            logging.WARNING
            if response.status_code >= status.HTTP_500_INTERNAL_SERVER_ERROR
            else logging.INFO,
            "%s %s %s %.1fms%s",
            request.method,
            route,
            response.status_code,
            duration_ms,
            f" {code}" if code is not None else "",
            extra={"fields": fields},
        )
        return response
