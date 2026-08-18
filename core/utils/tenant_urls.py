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

``get_tenant_api_base_url`` is the API-origin sibling: for links that
target a Django endpoint directly (no Nuxt proxy), e.g. unsubscribe /
subscription-confirmation. Falls back to ``settings.API_BASE_URL``.
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

    Always returns a URL without trailing slash. Defensive against
    tenants that don't expose ``.domains`` (e.g. test fakes, or
    transient states during tenant creation) — falls through to the
    settings value in that case rather than raising.
    """
    tenant = getattr(connection, "tenant", None)
    domains_manager = getattr(tenant, "domains", None) if tenant else None
    if domains_manager is not None:
        try:
            domain_obj = domains_manager.filter(is_primary=True).first()
        except Exception:  # noqa: BLE001 — any failure falls through to fallback
            domain_obj = None
        if domain_obj and getattr(domain_obj, "domain", ""):
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


def get_tenant_api_base_url() -> str:
    """Return the base URL for the current tenant's API host.

    Distinct from :func:`get_tenant_base_url`, which resolves the
    *storefront* (Nuxt) origin. Some outbound links (e.g. the
    newsletter unsubscribe / subscription-confirmation endpoints)
    point straight at a Django API route with no Nuxt proxy in front
    of it, so they must resolve against the tenant's API origin
    instead — a storefront-host link would 404, and the platform-wide
    ``settings.API_BASE_URL`` would send every non-platform tenant's
    recipients to the wrong tenant's API (and, for the signed
    unsubscribe token, a guaranteed schema-mismatch rejection).

    Resolution order:
    1. An explicit ``TenantDomain`` row whose ``domain`` starts with
       ``"api"`` (case-insensitive) — covers both the production
       convention (``api.<primary-domain>``) and shapes like
       ``api-staging.webside.gr`` that don't follow the
       ``api.<primary>`` pattern.
    2. ``api.<primary domain>`` derived from the tenant's primary
       domain — the infra TEMPLATE provisions this subdomain for
       every tenant even before an explicit row exists.
    3. ``settings.API_BASE_URL`` as a platform-wide fallback (public
       schema, missing tenant, or a tenant with no domains at all).

    Always returns a URL without trailing slash. Defensive against
    tenants that don't expose ``.domains`` — falls through to the
    settings value rather than raising.
    """
    api_domain = resolve_tenant_api_domain(getattr(connection, "tenant", None))
    if api_domain:
        return f"https://{api_domain}"

    fallback = getattr(settings, "API_BASE_URL", "") or ""
    return fallback.rstrip("/")


def resolve_tenant_api_domain(tenant) -> str:
    """Return the bare API hostname for *tenant*, or ``""``.

    Shared by :func:`get_tenant_api_base_url` (which reads
    ``connection.tenant``) and ``TenantConfigSerializer.api_domain``
    (which serializes an arbitrary tenant instance — the resolve
    endpoint answers for whichever domain was queried, not the
    request's own tenant).

    Resolution order:
    1. An explicit ``TenantDomain`` row whose ``domain`` starts with
       ``"api"`` (case-insensitive) — covers both the production
       convention (``api.<primary-domain>``) and shapes like
       ``api-staging.webside.gr``.
    2. ``api.<primary domain>`` derived from the primary domain — the
       infra TEMPLATE provisions this subdomain for every tenant even
       before an explicit row exists.

    Defensive against tenants without ``.domains`` (test fakes,
    transient creation states) — returns ``""`` rather than raising.
    """
    domains_manager = getattr(tenant, "domains", None) if tenant else None
    if domains_manager is None:
        return ""

    try:
        api_domain_obj = (
            domains_manager.filter(domain__istartswith="api")
            .order_by("-is_primary")
            .first()
        )
    except Exception:  # noqa: BLE001 — any failure falls through
        api_domain_obj = None
    if api_domain_obj and getattr(api_domain_obj, "domain", ""):
        return api_domain_obj.domain

    try:
        primary_domain_obj = domains_manager.filter(is_primary=True).first()
    except Exception:  # noqa: BLE001 — any failure falls through
        primary_domain_obj = None
    if primary_domain_obj and getattr(primary_domain_obj, "domain", ""):
        return f"api.{primary_domain_obj.domain}"

    return ""
