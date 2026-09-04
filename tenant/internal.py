"""Internal-service surface: shared helpers + host-agnostic middleware.

Cluster-internal consumers (the media-stream service) dial
``backend-service`` directly — a Host no ``TenantDomain`` row can
match, so ``TenantMainMiddleware`` would 404 the request before any
view runs. ``InternalDomainsMiddleware`` answers the internal domains
feed BEFORE tenant resolution (same placement rationale as
``HealthProbeMiddleware``): the endpoint is internal-token
authenticated and reads only SHARED tables, so the request host is
irrelevant to it. The DRF view in ``tenant/views.py`` shares the same
helpers and keeps serving host-routed callers (and the test client).
"""

from __future__ import annotations

from secrets import compare_digest

from django.conf import settings
from django.http import JsonResponse

INTERNAL_DOMAINS_PATH = "/api/v1/tenant/internal/domains"


def is_internal_caller(token: str) -> bool:
    """Constant-time check of the internal-services token (the same
    secret the agent gateway presents on tenant resolve)."""
    secret = settings.AGENT_GATEWAY_INTERNAL_SECRET
    return bool(secret) and compare_digest(token or "", secret)


def build_domains_payload() -> dict:
    """Active tenants' domains + derived api./assets./static. service
    subdomains of each primary domain (the infra TEMPLATE provisions
    those for every tenant)."""
    from tenant.models import TenantDomain

    rows = TenantDomain.objects.filter(
        tenant__is_active=True, tenant__suspended_at__isnull=True
    ).values_list("domain", "is_primary")

    domains: set[str] = set()
    for domain, is_primary in rows:
        domains.add(domain)
        if is_primary:
            domains.update(
                {f"api.{domain}", f"assets.{domain}", f"static.{domain}"}
            )
    return {"domains": sorted(domains)}


class InternalDomainsMiddleware:
    """Serve the internal domains feed host-agnostically.

    MIDDLEWARE placement: after ``HealthProbeMiddleware``, BEFORE
    ``TenantMainMiddleware``.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == "GET" and request.path == INTERNAL_DOMAINS_PATH:
            token = request.headers.get("X-Internal-Token", "")
            if not is_internal_caller(token):
                # Same posture as the DRF view: existence not advertised.
                return JsonResponse({"detail": "Not found."}, status=404)
            return JsonResponse(build_domains_payload())
        return self.get_response(request)
