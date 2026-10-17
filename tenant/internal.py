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
    # Compare BYTES, not str: `compare_digest` raises TypeError on a
    # str containing a non-ASCII character, and Django decodes header
    # bytes with latin-1 — so any byte >= 0x80 in the header turned a
    # "withhold and 404" into an unhandled 500, which is both an
    # existence oracle for an endpoint that deliberately hides itself
    # and a stream of fake incidents in the error tracker.
    return bool(secret) and compare_digest(
        (token or "").encode("utf-8", "surrogateescape"),
        secret.encode("utf-8", "surrogateescape"),
    )


def build_domains_payload() -> dict:
    """The tenant state media-stream serves by.

    Both lists follow the one rule ``SuspendedTenantMiddleware`` applies
    to a store's own requests, ``is_active``, so every store is in
    exactly one of them.

    ``domains``: active tenants' domains + derived api./assets./static.
    service subdomains of each primary domain (the infra TEMPLATE
    provisions those for every tenant).

    ``suspendedSchemas``: the stores that must not be served. media-stream
    refuses their images. A deny list, not an allow list: a store created
    between two polls is served at once, and an empty list (or a feed
    media-stream cannot read) refuses nothing.
    """
    from django_tenants.utils import get_public_schema_name

    from tenant.models import Tenant, TenantDomain

    rows = TenantDomain.objects.filter(tenant__is_active=True).values_list(
        "domain", "is_primary"
    )

    domains: set[str] = set()
    for domain, is_primary in rows:
        domains.add(domain)
        if is_primary:
            domains.update(
                {f"api.{domain}", f"assets.{domain}", f"static.{domain}"}
            )
    suspended = (
        Tenant.objects.filter(is_active=False)
        .exclude(schema_name=get_public_schema_name())
        .values_list("schema_name", flat=True)
    )
    return {"domains": sorted(domains), "suspendedSchemas": sorted(suspended)}


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
