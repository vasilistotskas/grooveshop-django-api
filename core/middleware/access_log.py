"""One INFO access line per request, with its latency.

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

Never logged: the raw path (it carries ids), the query string, headers,
cookies, bodies. The route is the URL PATTERN the request resolved to.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

#: ``route`` of a request no URL pattern matched. A fixed label rather
#: than the raw path, so a scanner walking random URLs cannot mint a new
#: value per request.
UNMATCHED_ROUTE = "unmatched"


class AccessLogMiddleware:
    """Log ``method route status duration_ms`` once per request.

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

        match = request.resolver_match
        route = UNMATCHED_ROUTE if match is None else match.route or "/"
        logger.info(
            "%s %s %s %.1fms",
            request.method,
            route,
            response.status_code,
            duration_ms,
            extra={
                "fields": {
                    "method": request.method,
                    "route": route,
                    "status": response.status_code,
                    "duration_ms": duration_ms,
                }
            },
        )
        return response
