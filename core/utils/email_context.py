"""Shared base context for every transactional/marketing email.

Every Celery email task previously rebuilt the same
``SITE_NAME``/``SITE_URL``/``INFO_EMAIL``/``STATIC_BASE_URL`` dict
inline, and none of them included ``SITE_LOGO_URL`` — the per-tenant
logo branch in ``core/templates/emails/base/email_base.html`` was
dead code, so every tenant's transactional emails silently rendered
the platform's static ``logo-dark.svg`` regardless of what the
merchant configured on ``Tenant.logo_light_url``. Route every email
context build through :func:`build_email_context` so the tenant logo
actually reaches outbound mail, and so new shared SITE_* keys only
need to be added in one place.
"""

from __future__ import annotations

from typing import Any

from urllib.parse import urlparse

from django.conf import settings
from django.db import connection

from core.utils.tenant_urls import (
    get_tenant_base_url,
    get_tenant_static_base_url,
)
from tenant.credentials import (
    tenant_contact_email,
    tenant_logo_url,
    tenant_site_name,
)


def build_email_context(**extra: Any) -> dict[str, Any]:
    """Return the shared template context for a transactional email.

    ``**extra`` are the caller's task-specific keys (e.g. ``order``,
    ``items``, ``unsubscribe_url``) merged on top — an ``extra`` key
    that collides with one of the shared keys below wins, so callers
    can still override e.g. ``INFO_EMAIL`` with a staff address for
    admin-facing notifications.
    """
    return {
        "SITE_NAME": tenant_site_name(),
        "SITE_URL": get_tenant_base_url(),
        "INFO_EMAIL": tenant_contact_email(),
        "STATIC_BASE_URL": get_tenant_static_base_url(),
        "SITE_LOGO_URL": _email_logo_url(),
        **extra,
    }


def _is_platform_tenant() -> bool:
    """True when the active tenant IS the platform's own storefront.

    Server-side twin of the Nuxt ``useIsPlatformTenant`` check: the
    tenant whose primary domain equals the platform base URL's host is
    the platform brand itself. Missing tenant/domain counts as platform
    so single-tenant setups keep today's behaviour.
    """
    tenant = getattr(connection, "tenant", None)
    domains_manager = getattr(tenant, "domains", None) if tenant else None
    if domains_manager is None:
        return True
    try:
        primary = domains_manager.filter(is_primary=True).first()
    except Exception:  # noqa: BLE001 — fall through to platform
        return True
    if primary is None or not getattr(primary, "domain", ""):
        return True
    base = getattr(settings, "NUXT_BASE_URL", "") or ""
    host = urlparse(base).hostname or ""
    return bool(host) and primary.domain == host


def _email_logo_url() -> str:
    """Logo for outbound email branding, tenant-scoped end-to-end.

    Tenant logo when set; the platform's static logo ONLY for the
    platform tenant; empty otherwise — ``email_base.html`` renders the
    store name as a text wordmark when empty, so an unbranded tenant's
    emails never wear another store's brand.
    """
    logo = tenant_logo_url()
    if logo:
        return logo
    if _is_platform_tenant():
        return f"{get_tenant_static_base_url()}/static/logo-dark.svg"
    return ""
