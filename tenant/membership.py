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


def tenant_plan_allows(flag: str) -> bool:
    """True when the active tenant's PLAN permits a feature.

    The plan flags on ``Tenant`` (``gift_cards_enabled`` and friends)
    are platform-controlled and describe what a store has PAID for. The
    matching ``extra_settings`` toggles are merchant-controlled and
    describe whether the store currently WANTS it on. Both must hold —
    see the contract in ``tenant/permissions.py``.

    That contract used to be enforced only by the DRF permission
    classes on each feature's own endpoints, which the order-create
    path never passes through: ``create`` is a public action and runs
    with ``permission_classes = []`` for guest checkout. Redemption and
    discounting therefore consulted only the merchant-editable setting,
    so a merchant on a plan WITHOUT gift cards could flip
    ``GIFT_CARDS_ENABLED`` in their own admin, issue a card, and have
    it redeemed at checkout. Folding the plan flag into each service's
    ``is_enabled()`` closes every entry point at once, because all of
    them funnel through those methods.

    ``getattr`` with a permissive default rather than a direct
    attribute read: under ``schema_context`` django-tenants attaches a
    ``FakeTenant`` carrying only ``schema_name``, so the plan fields are
    absent. Failing open there is deliberate — a background task must
    not start refusing legitimate work because of how its schema was
    entered. The path that matters is the HTTP one, where
    ``TenantMainMiddleware`` attaches a real ``Tenant`` row.
    """
    tenant = get_current_tenant()
    if tenant is None:
        # Public schema — platform routines are not plan-gated.
        return True
    return bool(getattr(tenant, flag, True))


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
