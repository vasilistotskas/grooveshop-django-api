"""Dependency-free K8s probe answers ahead of tenant resolution.

Kubelet probes hit the pod IP directly, so the ``Host`` header is the
pod address — a host ``TenantMainMiddleware`` can never map to a
``TenantDomain`` row. Without this middleware every readiness probe
404s ("no tenant for hostname") and the pod is de-registered even
though the worker is perfectly healthy.

Answered here, before ``TenantMainMiddleware``, the probe exercises
the full ASGI → Django middleware entry path while touching no
backing service (no DB tenant lookup, no Redis) — the same contract as
``core.api.views.health_live``, which stays mounted for documentation
and any probe that DOES send a real tenant host. The root ``/health/``
liveness probe is answered even earlier, at the ASGI layer
(``asgi.health_check``).
"""

from __future__ import annotations

from django.http import JsonResponse

PROBE_PATHS = frozenset({"/api/v1/health/live"})


class HealthProbeMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path in PROBE_PATHS:
            return JsonResponse({"status": "ok"})
        return self.get_response(request)
