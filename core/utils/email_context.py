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
        "SITE_LOGO_URL": tenant_logo_url(),
        **extra,
    }
