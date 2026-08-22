"""Tenant membership helpers.

``UserTenantMembership`` grants a PLATFORM-PUBLIC identity operator or
staff access over a tenant. It is NOT how customers are scoped to a
store: shoppers live in their tenant's own schema (``user`` is in both
SHARED_APPS and TENANT_APPS, and the tenant copy wins on the search
path), so a shopper registered at tenant A has no row, no allauth
records and no Knox token in tenant B. That separation is structural
and needs no check.

Requiring membership of customers was actively harmful: the table lives
in the public schema with an FK to ``public.user_useraccount``, so a
shopper created in a tenant schema could not have one — the grant that
tried to create it raised ForeignKeyViolation and made signup 500,
leaving orphaned accounts that could never log in.

So: use ``get_membership`` on STAFF surfaces (the admin's
``has_permission``, ``TenantRolePermissionBackend``). For ordinary
authenticated endpoints use DRF's
``IsAuthenticated``/``IsAuthenticatedOrReadOnly`` — being authenticated
in this schema IS the authorization.

A ``HasTenantAccess`` DRF permission used to live here. It was
unsound on an API request — the membership lookup compared primary
keys ACROSS schemas (an API session authenticates against the TENANT
schema while the FK targets public) — and every consumer has since
moved to the role-derived classes in ``core.api.permissions``, which
grant only to provenance-stamped platform identities. See
``docs/api-staff-identity.md``.
"""

from __future__ import annotations

from typing import Any

from django.db import connection


def get_current_tenant() -> Any | None:
    """Return the tenant django-tenants attached to this connection.

    Returns None when called from the public schema (admin paths,
    platform routines) so callers can early-return.
    """
    tenant = getattr(connection, "tenant", None)
    if tenant is None:
        return None
    if getattr(tenant, "schema_name", "public") == "public":
        return None
    return tenant


def get_membership(user: Any, tenant: Any | None = None) -> Any | None:
    """Return the active membership for user+tenant, or None."""
    if user is None or not getattr(user, "is_authenticated", False):
        return None

    tenant = tenant or get_current_tenant()
    if tenant is None:
        return None

    from tenant.models import UserTenantMembership

    return (
        UserTenantMembership.objects.filter(
            user=user,
            tenant=tenant,
            is_active=True,
        )
        .only("id", "role", "tenant_id", "user_id")
        .first()
    )
