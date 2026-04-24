"""Tenant-aware frontend URL helpers.

Every outbound email, push notification, or SMS that includes a link back
to the storefront must use the domain of the tenant that owns the request
(not the single platform-wide ``NUXT_BASE_URL``). Otherwise a tenant-B
user gets a confirmation email with a link that goes to webside.gr.

``get_tenant_frontend_url`` reads ``connection.tenant`` set by
django-tenants' ``TenantMainMiddleware`` (or by ``TenantTask`` for
Celery tasks) and builds an absolute URL against that tenant's primary
domain. Falls back to ``settings.NUXT_BASE_URL`` so callers that might
run in the public schema or under misconfiguration still produce a valid
URL.
"""

from __future__ import annotations

from django.conf import settings
from django.db import connection


def get_tenant_base_url() -> str:
    """Return the base URL for the current tenant's storefront.

    Resolution order:
    1. The primary domain of ``connection.tenant`` (usually set by
       ``TenantMainMiddleware`` or ``TenantTask`` in Celery).
    2. ``settings.NUXT_BASE_URL`` as a platform-wide fallback.

    Always returns a URL without trailing slash.
    """
    tenant = getattr(connection, "tenant", None)
    if tenant is not None:
        domain_obj = tenant.domains.filter(is_primary=True).first()
        if domain_obj and domain_obj.domain:
            return f"https://{domain_obj.domain}"

    fallback = getattr(settings, "NUXT_BASE_URL", "") or ""
    return fallback.rstrip("/")


def get_tenant_frontend_url(path: str = "") -> str:
    """Join ``path`` onto the current tenant's storefront base URL.

    Example: ``get_tenant_frontend_url("/account/orders/42")`` returns
    ``https://webside.gr/account/orders/42`` on the webside tenant and
    ``https://tenant-b.com/account/orders/42`` on tenant-b.
    """
    base = get_tenant_base_url()
    if not path:
        return base
    if not path.startswith("/"):
        path = "/" + path
    return f"{base}{path}"
