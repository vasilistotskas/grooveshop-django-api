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
URL. It also takes the link's language and adds the storefront's locale
prefix for it (``storefront_locale_prefix``), so an English email opens
the English page. ``get_tenant_base_url`` is the bare origin, for callers
that need a host rather than a page.

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

from urllib.parse import urlsplit, urlunsplit

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import connection
from django.utils.translation import gettext_lazy as _


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
        except Exception:
            domain_obj = None
        if domain_obj and getattr(domain_obj, "domain", ""):
            return f"https://{domain_obj.domain}"

    fallback = getattr(settings, "NUXT_BASE_URL", "") or ""
    return fallback.rstrip("/")


# The storefront's URL-language contract, mirrored here. Keep in step with
# the storefront repository, which owns it:
#
# - ``STOREFRONT_LOCALES`` is ``SUPPORTED_LOCALES`` in its
#   ``i18n/locales.ts``: the locales it generates routes for at build time.
#   Deliberately narrower than ``settings.LANGUAGES`` — Django carries
#   ``de`` content, the storefront has no ``/de`` routes, so a ``/de`` link
#   would 404.
# - ``STOREFRONT_DEFAULT_LOCALE`` is ``DEFAULT_LOCALE`` in the same file:
#   the UNPREFIXED locale. @nuxtjs/i18n runs ``prefix_except_default``, so
#   that locale's pages carry no prefix and every other locale's pages
#   live under ``/<code>``. A build-time constant there, so a constant
#   here — never ``settings.LANGUAGE_CODE``, which env can override
#   without rebuilding the storefront.
#
# Changing the storefront's default locale or its locale list means
# changing this block in the same release.
STOREFRONT_LOCALES: tuple[str, ...] = ("el", "en")
STOREFRONT_DEFAULT_LOCALE = "el"


def tenant_storefront_locales(tenant) -> tuple[str, ...]:
    """The locales *tenant*'s storefront is reachable in.

    The storefront's ``shared/i18n/tenantLocales.ts:tenantAllowedLocales``,
    rule for rule, because a link has to agree with the storefront's
    ``locale-available`` route middleware, which 404s any other prefix:
    ``Tenant.available_locales`` filtered to :data:`STOREFRONT_LOCALES`;
    empty means ``[default_locale]``; no tenant at all, or a default the
    storefront does not support, means every storefront locale.
    """
    if tenant is None:
        return STOREFRONT_LOCALES
    listed = tuple(
        code
        for code in (getattr(tenant, "available_locales", None) or [])
        if code in STOREFRONT_LOCALES
    )
    if listed:
        return listed
    default = getattr(tenant, "default_locale", None)
    if default in STOREFRONT_LOCALES:
        return (default,)
    return STOREFRONT_LOCALES


def storefront_locale_prefix(tenant, language: str) -> str:
    """The path prefix that opens *tenant*'s storefront in *language*.

    ``"/<language>"`` when the tenant serves that language and it is not
    :data:`STOREFRONT_DEFAULT_LOCALE`; ``""`` otherwise, which is the
    storefront's default-locale page. The one place the rule lives —
    every storefront link goes through it.
    """
    if language == STOREFRONT_DEFAULT_LOCALE:
        return ""
    if language not in tenant_storefront_locales(tenant):
        return ""
    return f"/{language}"


def get_tenant_frontend_url(path: str, *, language: str) -> str:
    """Absolute URL of storefront *path*, in *language*, on the current
    tenant's primary domain.

    *language* is the language of whatever carries the link — for an
    email, the one it is rendered in — so the page it opens reads the
    same. ``get_tenant_frontend_url("/account/orders/42", language="en")``
    is ``https://webside.gr/en/account/orders/42`` on a tenant that serves
    English, and ``https://webside.gr/account/orders/42`` for ``el`` or on
    a Greek-only tenant. An empty *path* is the storefront home page.
    """
    if path and not path.startswith("/"):
        path = "/" + path
    prefix = storefront_locale_prefix(
        getattr(connection, "tenant", None), language
    )
    return f"{get_tenant_base_url()}{prefix}{path}"


def localize_storefront_url(url: str, *, language: str) -> str:
    """Put *language*'s prefix on an absolute storefront *url* built
    without one.

    For links built by a library rather than by
    :func:`get_tenant_frontend_url` — allauth resolves its email links
    from ``HEADLESS_FRONTEND_URLS`` before the email's language is known
    (see ``UserAccountAdapter.send_mail``). Same rule, same tenant.
    """
    parts = urlsplit(url)
    prefix = storefront_locale_prefix(
        getattr(connection, "tenant", None), language
    )
    if not prefix:
        return url
    return urlunsplit(parts._replace(path=f"{prefix}{parts.path}"))


def validate_storefront_path(value: str) -> None:
    """Validator for a locale-neutral storefront path.

    Accepts ``/account/orders/42``, optionally with a query and a
    fragment. Rejects anything that names a host or a scheme (a leading
    ``//`` included), whitespace, and a leading locale segment
    (``/en/...``): the path must open in whatever language its viewer is
    browsing, which the storefront adds when the link is followed.
    """
    if not value.startswith("/") or value.startswith("//"):
        raise ValidationError(
            _("Enter a storefront path starting with a single '/'."),
            code="invalid_storefront_path",
        )
    if any(char.isspace() for char in value):
        raise ValidationError(
            _("A storefront path cannot contain whitespace."),
            code="invalid_storefront_path",
        )
    path = urlsplit(value).path
    if path.split("/")[1] in STOREFRONT_LOCALES:
        raise ValidationError(
            _("A storefront path cannot start with a locale prefix."),
            code="invalid_storefront_path",
        )


def storefront_path(path: str) -> str:
    """A locale-neutral storefront path, for a link whose viewer's
    language is not known when it is written — an in-app notification,
    shown in whatever language the recipient is browsing. The storefront
    applies that locale when the link is clicked (``useLocalePath``).

    The frozen webside ``NotificationsBell.vue`` navigates the path as-is,
    which is exactly right there: webside serves ``el`` only, the
    unprefixed locale.

    Raises ``ValidationError`` for anything
    :func:`validate_storefront_path` rejects.
    """
    validate_storefront_path(path)
    return path


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
    except Exception:
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
    except Exception:
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
