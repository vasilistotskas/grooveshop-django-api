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

from django.db import connection
from django_tenants.utils import get_public_schema_name

from core.utils.tenant_urls import (
    get_tenant_base_url,
    get_tenant_static_base_url,
)
from tenant.credentials import (
    tenant_contact_email,
    tenant_email_theme,
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
        # Brand colours as literal hex — email_base.html renders them
        # straight into its <style> block. See tenant_email_theme for
        # why CSS custom properties cannot be used here.
        "THEME": tenant_email_theme(),
        **extra,
    }


def _is_platform_tenant() -> bool:
    """True when the active tenant IS the platform's own storefront.

    Server-side twin of the Nuxt ``useIsPlatformTenant`` check, and the
    same source of truth: the ``Tenant.is_platform_storefront`` row
    flag. No hostname comparison — the previous ``NUXT_BASE_URL`` match
    made a platform env value carry tenant #1's domain. No tenant, or
    the public schema (admin and platform contexts), counts as platform
    so those emails keep the platform brand.
    """
    tenant = getattr(connection, "tenant", None)
    schema = getattr(tenant, "schema_name", "") if tenant else ""
    if not schema or schema == get_public_schema_name():
        return True
    return bool(getattr(tenant, "is_platform_storefront", False))


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
