from __future__ import annotations

from django.core.cache import cache
from django.db import connection
from django.middleware.csrf import CsrfViewMiddleware

TENANT_DOMAINS_CACHE_TTL = 300  # 5 minutes


class TenantCsrfMiddleware(CsrfViewMiddleware):
    """Dynamic CSRF trusted origins per tenant domain."""

    def _origin_verified(self, request):
        if super()._origin_verified(request):
            return True

        tenant = getattr(connection, "tenant", None)
        if tenant is None:
            return False

        origin = request.META.get("HTTP_ORIGIN", "")
        if not origin:
            return False

        cache_key = f"tenant_domains:{tenant.schema_name}"
        domains = cache.get(cache_key)
        if domains is None:
            domains = set(tenant.domains.values_list("domain", flat=True))
            cache.set(cache_key, domains, TENANT_DOMAINS_CACHE_TTL)

        return any(origin in (f"https://{d}", f"http://{d}") for d in domains)
