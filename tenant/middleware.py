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


class TenantCookieDomainMiddleware:
    """Per-request session/CSRF cookie domains.

    ``SESSION_COOKIE_DOMAIN`` / ``CSRF_COOKIE_DOMAIN`` are process-wide
    settings pinned to the platform apex (``.webside.gr``) — with them,
    a tenant on its OWN domain can never complete the cross-subdomain
    cookie round-trip (``tenant.com`` ↔ ``api.tenant.com``). This
    middleware rewrites the ``Domain`` attribute of the session/CSRF
    ``Set-Cookie`` headers to the value derived from the request host:

        api.acme.com  → .acme.com
        www.acme.com  → .acme.com
        acme.com      → .acme.com

    Every non-public request host IS a ``TenantDomain`` row (that's how
    the schema resolved), so stripping a single well-known service
    label and dot-prefixing yields exactly the tenant's registrable
    scope — including nested setups like ``shop.webside.gr`` →
    ``.shop.webside.gr`` staying isolated from the platform apex.

    Public-schema requests (platform admin on the platform API host)
    keep the configured settings values untouched.

    MIDDLEWARE placement: directly after ``TenantMainMiddleware`` — the
    response phase runs in reverse order, so this rewrites cookies
    AFTER Session/CSRF middleware have set them.
    """

    _STRIP_LABELS = ("api.", "www.")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        from django.db import connection as _connection

        tenant = getattr(_connection, "tenant", None)
        if (
            tenant is None
            or getattr(tenant, "schema_name", "public") == "public"
            or not response.cookies
        ):
            return response

        host = request.get_host().split(":")[0]
        for label in self._STRIP_LABELS:
            if host.startswith(label):
                host = host[len(label) :]
                break
        cookie_domain = f".{host}"

        from django.conf import settings as django_settings

        rewrite_names = {
            django_settings.SESSION_COOKIE_NAME,
            django_settings.CSRF_COOKIE_NAME,
        }
        for name, morsel in response.cookies.items():
            if name in rewrite_names:
                morsel["domain"] = cookie_domain
        return response
