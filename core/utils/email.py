from __future__ import annotations

from typing import Any

from django.db import connection
from disposable_email_domains import blocklist as DISPOSABLE_BLOCKLIST

from core.utils.tenant_urls import (
    get_tenant_base_url,
    get_tenant_static_base_url,
)
from tenant.credentials import tenant_contact_email, tenant_site_name


def is_disposable_domain(domain: str) -> bool:
    domain = domain.lower()
    if domain in DISPOSABLE_BLOCKLIST:
        return True
    parts = domain.split(".")
    for i in range(1, len(parts) - 1):
        if ".".join(parts[i:]) in DISPOSABLE_BLOCKLIST:
            return True
    return False


def get_base_email_context() -> dict[str, Any]:
    """Shared context keys injected into every transactional/marketing email.

    ``SITE_URL`` resolves to the primary domain of the currently-active
    tenant (via django-tenants connection state) so the link in a
    tenant-B user's email points to tenant-B, not webside.gr.

    ``INFO_EMAIL`` resolves to the per-tenant contact address with a
    three-tier fallback: Tenant.contact_email → CONTACT_EMAIL
    extra_setting → settings.INFO_EMAIL.

    ``SITE_NAME`` resolves to ``Tenant.name`` with a fallback to
    ``settings.SITE_NAME`` (see ``tenant.credentials.tenant_site_name``).

    ``SITE_LOGO_URL`` resolves to the tenant's light-theme logo (the
    variant meant for a light/white background, matching how the
    storefront renders it — see ``Tenant.Logo.vue``'s ``dark:hidden``
    default) with a fallback to the static ``logo-dark.svg`` asset.
    """
    tenant = getattr(connection, "tenant", None)
    static_base_url = get_tenant_static_base_url()
    logo_url = getattr(tenant, "logo_light_url", "") or ""
    if not logo_url:
        logo_url = f"{static_base_url}/static/logo-dark.svg"
    return {
        "SITE_NAME": tenant_site_name(),
        "SITE_URL": get_tenant_base_url(),
        "INFO_EMAIL": tenant_contact_email(),
        "STATIC_BASE_URL": static_base_url,
        "SITE_LOGO_URL": logo_url,
    }
