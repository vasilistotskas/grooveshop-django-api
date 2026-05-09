"""Tenant membership helpers.

Checks that bridge django-tenants' schema awareness with the
``UserTenantMembership`` table. Pattern: every tenant-scoped API view
and signal path calls ``require_tenant_access`` (or the DRF permission
below) to refuse requests where the authenticated user has no active
membership in the current ``connection.tenant``.
"""

from __future__ import annotations

from typing import Any

from django.db import connection
from rest_framework import permissions


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


def user_has_tenant_access(user: Any, tenant: Any | None = None) -> bool:
    """True if the user has an active membership in the current tenant.

    Platform superusers are intentionally NOT granted automatic access —
    they manage tenants from the public-schema admin, not by borrowing
    tenant-schema rows. If a platform operator needs to log into a
    tenant to debug, they get a regular membership provisioned.
    """
    return get_membership(user, tenant) is not None


class HasTenantAccess(permissions.BasePermission):
    """DRF permission that refuses requests without a tenant membership.

    Use on any viewset that reads/writes tenant-scoped data. Read-only
    public data (e.g. Tenant.resolve, health) should use AllowAny
    instead — this permission is for authenticated tenant-scoped APIs.
    """

    message = "You do not have access to this store."

    def has_permission(self, request, view):
        return user_has_tenant_access(request.user)


class IsTenantMemberOrReadOnly(permissions.BasePermission):
    """Drop-in replacement for ``IsAuthenticatedOrReadOnly`` that also
    validates tenant membership for authenticated requests.

    Rules:
    - Anonymous requests on SAFE_METHODS (GET, HEAD, OPTIONS): allowed.
    - Authenticated requests: user must have an active ``UserTenantMembership``
      for the current ``connection.tenant``.  If the connection is in the
      public schema (no tenant attached) the membership check is skipped
      and the request is allowed — this preserves correct behaviour for
      platform-admin and health-check paths that run in the public schema.

    This permission is used as the global default in ``DEFAULT_PERMISSION_CLASSES``
    so that every new ViewSet inherits secure-by-default behaviour without
    requiring explicit per-viewset annotations.  ViewSets that need fully
    anonymous write access (e.g. guest checkout, contact form) must override
    ``permission_classes = [AllowAny]`` explicitly.
    """

    message = "You do not have access to this store."

    def has_permission(self, request, view):
        # Allow all safe (read-only) requests from anonymous users.
        if request.method in permissions.SAFE_METHODS:
            return True

        # Unauthenticated write attempt — deny.
        if not getattr(request.user, "is_authenticated", False):
            return False

        # Authenticated request — require tenant membership when a tenant
        # is active on this connection.
        tenant = get_current_tenant()
        if tenant is None:
            # Public-schema path (admin, health, platform routines).
            return True

        return user_has_tenant_access(request.user, tenant)
