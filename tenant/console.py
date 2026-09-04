"""Helpers for telling the PLATFORM console apart from a tenant's admin.

``platform.grooveshop.space`` serves the PUBLIC schema — the control
plane where tenants, memberships and platform settings are managed. Every
other admin host serves one tenant's own schema. The two need different
branding, a different sidebar and a different landing page, so the
distinction is defined once here rather than re-derived at each call
site.

Detection is POSITIVE-knowledge only, matching
``BaseModelAdmin._withheld_on_public``: it reads ``request.tenant`` (set
by django-tenants' TenantMainMiddleware) and answers False whenever that
is absent. Management commands, Celery tasks and tests run with no
request and no bound tenant; treating that state as "platform" would
silently reshape the admin for them.
"""

from __future__ import annotations

from typing import Any


def is_platform_console(request: Any) -> bool:
    """True when *request* is being served from the PUBLIC schema."""
    from django_tenants.utils import get_public_schema_name

    tenant = getattr(request, "tenant", None)
    if tenant is None:
        return False
    return getattr(tenant, "schema_name", None) == get_public_schema_name()


def is_tenant_console(request: Any) -> bool:
    """True when *request* is being served from a TENANT schema.

    Not simply ``not is_platform_console`` — both are False when the
    schema is unknown, so a caller can distinguish "definitely a tenant"
    from "no idea".
    """
    from django_tenants.utils import get_public_schema_name

    tenant = getattr(request, "tenant", None)
    if tenant is None:
        return False
    return getattr(tenant, "schema_name", None) != get_public_schema_name()
