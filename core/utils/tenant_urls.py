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

``get_tenant_assets_base_url``/``get_tenant_static_base_url`` are the
media-processing (``assets.``) and static-file (``static.``) origin
siblings — used for absolute media/static URLs in transactional emails
and other tenant-scoped, non-browser contexts. Unlike the merchant
CREDENTIAL helpers in ``tenant/credentials.py`` (Stripe, Viva Wallet,
ACS, BoxNow, Meta CAPI — tenant-only, no fallback, because those are
money/secrets), these two fall back to
``settings.MEDIA_STREAM_BASE_URL``/``settings.STATIC_BASE_URL`` when
there is no active tenant (public schema, management commands, Celery
workers without a TenantTask). That fallback is deliberate: the
media-stream/static services are shared PLATFORM INFRASTRUCTURE
endpoints, not per-merchant credentials — the platform origin is a
valid, safe answer for public-schema/admin contexts, unlike a
platform Stripe key which must never silently bill the wrong account.
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


def _resolve_prefixed_service_domain(
    tenant, prefix: str, *, derive: bool = True
) -> str:
    """Return the bare ``<prefix>.<primary-domain>``-shaped hostname for
    *tenant*, or ``""``. Shared implementation behind
    :func:`resolve_tenant_api_domain`, :func:`resolve_tenant_assets_domain`,
    and :func:`resolve_tenant_static_domain`.

    Resolution order:
    1. An explicit ``TenantDomain`` row whose ``domain`` starts with
       *prefix* (case-insensitive) — covers both the production
       convention (``<prefix>.<primary-domain>``) and shapes like
       ``api-staging.webside.gr`` that don't follow the
       ``<prefix>.<primary>`` pattern.
    2. Only when ``derive`` is True: ``<prefix>.<primary domain>``
       derived from the tenant's primary domain. The API host is the
       only prefix that derives — every tenant MUST have its own api
       origin (browser-facing auth/WebSocket/OAuth surfaces), so its
       DNS is a mandatory onboarding step. Asset/static hosts are
       platform-shared by default (tenancy is enforced at the PATH
       level — ``media/{schema}/…``); a dedicated asset origin is a
       white-label OPT-IN via an explicit prefixed row.

    Defensive against tenants without ``.domains`` (test fakes,
    transient creation states) — returns ``""`` rather than raising.
    """
    domains_manager = getattr(tenant, "domains", None) if tenant else None
    if domains_manager is None:
        return ""

    try:
        candidates = list(
            domains_manager.filter(domain__istartswith=prefix).order_by(
                "-is_primary"
            )
        )
    except Exception:  # noqa: BLE001 — any failure falls through
        candidates = []

    # Require a SEPARATOR after the prefix. ``istartswith`` alone matched
    # any domain merely beginning with those letters, so a tenant on
    # ``apiary.gr`` resolved its own storefront domain as its API host.
    # Both separators are in real use: production runs ``api.webside.gr``
    # while staging runs ``api-staging.webside.gr``. Filtered here rather
    # than in SQL because a tenant has a handful of domains and this
    # keeps the query one shape.
    prefixed_domain_obj = next(
        (
            row
            for row in candidates
            if _has_prefix_boundary(getattr(row, "domain", ""), prefix)
        ),
        None,
    )
    if prefixed_domain_obj and getattr(prefixed_domain_obj, "domain", ""):
        return prefixed_domain_obj.domain

    if not derive:
        return ""

    try:
        primary_domain_obj = domains_manager.filter(is_primary=True).first()
    except Exception:  # noqa: BLE001 — any failure falls through
        primary_domain_obj = None
    if primary_domain_obj and getattr(primary_domain_obj, "domain", ""):
        return f"{prefix}.{primary_domain_obj.domain}"

    return ""


def _has_prefix_boundary(domain: str, prefix: str) -> bool:
    """True when *domain* starts with *prefix* followed by a separator.

    ``api.webside.gr`` and ``api-staging.webside.gr`` qualify;
    ``apiary.gr`` does not.
    """
    lowered = (domain or "").lower()
    prefix = prefix.lower()
    if not lowered.startswith(prefix):
        return False
    rest = lowered[len(prefix) :]
    return rest[:1] in (".", "-")


def resolve_tenant_api_domain(tenant) -> str:
    """Return the bare API hostname for *tenant*, or ``""``.

    Shared by :func:`get_tenant_api_base_url` (which reads
    ``connection.tenant``) and ``TenantConfigSerializer.api_domain``
    (which serializes an arbitrary tenant instance — the resolve
    endpoint answers for whichever domain was queried, not the
    request's own tenant).
    """
    return _resolve_prefixed_service_domain(tenant, "api")


def resolve_tenant_assets_domain(tenant) -> str:
    """Return the bare media/image-processing hostname for *tenant*, or
    ``""``.

    Shared by :func:`get_tenant_assets_base_url` and
    ``TenantConfigSerializer.assets_domain``. Explicit-row ONLY (no
    derivation): the media service is platform infrastructure and
    tenancy is enforced at the path level, so tenants share the
    platform asset origin unless a dedicated white-label host was
    provisioned as an ``assets*`` ``TenantDomain`` row.
    """
    return _resolve_prefixed_service_domain(tenant, "assets", derive=False)


def resolve_tenant_static_domain(tenant) -> str:
    """Return the bare static-file hostname for *tenant*, or ``""``.

    Shared by :func:`get_tenant_static_base_url` and
    ``TenantConfigSerializer.static_domain``. Explicit-row ONLY (no
    derivation) — same platform-shared-by-default policy as
    :func:`resolve_tenant_assets_domain`.
    """
    return _resolve_prefixed_service_domain(tenant, "static", derive=False)


def get_tenant_assets_base_url() -> str:
    """Return the base URL for the current tenant's media/image origin.

    Used to build absolute media-processing URLs (e.g. the
    ``media_stream-image`` suffix product/user images resolve through)
    in tenant-scoped, non-browser contexts such as transactional
    emails. Falls back to ``settings.MEDIA_STREAM_BASE_URL`` when there
    is no active tenant — see the module docstring for why that
    fallback is safe here (platform infra endpoint, not a merchant
    credential).

    Always returns a URL without trailing slash.
    """
    assets_domain = resolve_tenant_assets_domain(
        getattr(connection, "tenant", None)
    )
    if assets_domain:
        return f"https://{assets_domain}"

    fallback = getattr(settings, "MEDIA_STREAM_BASE_URL", "") or ""
    return fallback.rstrip("/")


def get_tenant_static_base_url() -> str:
    """Return the base URL for the current tenant's static-file origin.

    Used to build absolute static-asset URLs (e.g. the fallback email
    logo, static icons referenced in transactional email templates) in
    tenant-scoped, non-browser contexts. Falls back to
    ``settings.STATIC_BASE_URL`` when there is no active tenant — see
    the module docstring for why that fallback is safe here (platform
    infra endpoint, not a merchant credential).

    Always returns a URL without trailing slash.
    """
    static_domain = resolve_tenant_static_domain(
        getattr(connection, "tenant", None)
    )
    if static_domain:
        return f"https://{static_domain}"

    fallback = getattr(settings, "STATIC_BASE_URL", "") or ""
    return fallback.rstrip("/")
