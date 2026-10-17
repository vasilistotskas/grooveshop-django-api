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
``has_permission``, ``TenantRolePermissionBackend``), and
``is_store_staff`` wherever an API code path asks "may this request
act as staff of the current store?". For ordinary authenticated
endpoints use DRF's ``IsAuthenticated``/``IsAuthenticatedOrReadOnly``
— being authenticated in this schema IS the authorization.

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
from django_tenants.utils import get_public_schema_name


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


_ROW_ATTR = "_resolved_tenant_row"


def bound_tenant() -> Any | None:
    """The REAL ``Tenant`` row bound to this connection, or None.

    Every reader of a per-tenant field (credentials, plan flags,
    branding, locales, Cloudflare zone) must come through here instead
    of reading ``connection.tenant`` directly. Under
    ``schema_context(name)`` -- a commit hook that re-enters its schema,
    a management command, a webhook loop -- django-tenants binds a bare
    ``FakeTenant`` carrying only ``schema_name``, so every field read
    off it is silently absent: a store with myDATA, Viva or ACS
    credentials reads as unconfigured and the work is skipped without an
    error. ``tenant_context(row)`` and ``TenantMainMiddleware`` bind the
    row itself, and that is returned as is.

    A ``FakeTenant`` on a store's schema is upgraded to its row by
    ``schema_name`` -- the lookup django-tenants itself uses -- and the
    row is remembered on that FakeTenant, so it lives exactly as long as
    the binding: one query per ``schema_context`` block, and the next
    block re-reads the credentials the merchant has just saved. A
    schema with no row, and the public schema under a FakeTenant, are no
    store: None, so a missing credential still reads as missing and
    nothing falls back to another tenant's or the platform's values.
    """
    return real_tenant(getattr(connection, "tenant", None))


def real_tenant(tenant: Any | None) -> Any | None:
    """*tenant* itself, or the row behind it when it is a ``FakeTenant``.

    The upgrade ``bound_tenant`` applies to the connection's tenant, for
    callers that already hold one (``get_current_tenant()``).
    """
    from django_tenants.postgresql_backend.base import FakeTenant
    from django_tenants.utils import get_public_schema_name

    if not isinstance(tenant, FakeTenant):
        return tenant
    if tenant.schema_name == get_public_schema_name():
        return None
    if _ROW_ATTR not in vars(tenant):
        from tenant.models import Tenant

        setattr(
            tenant,
            _ROW_ATTR,
            Tenant.objects.filter(schema_name=tenant.schema_name).first(),
        )
    return vars(tenant)[_ROW_ATTR]


def resolve_current_tenant() -> Any | None:
    """The real ``Tenant`` row for the active schema, or None on public.

    ``get_current_tenant`` hands back whatever django-tenants bound to
    the connection, which under ``schema_context`` is a bare
    ``FakeTenant``. Callers that need a real field (``plan``,
    ``vertical``) use this; see ``bound_tenant``.
    """
    if get_current_tenant() is None:
        return None
    return bound_tenant()


def is_demo_store() -> bool:
    """True when the bound store is a demo store (``Tenant.is_demo``).

    Read like every per-tenant flag, off the real row with a default: a
    tenant stand-in that never modelled the field (and the public schema,
    which has no row) is not a demo store, so a path that merely asks
    cannot fail on it.
    """
    return bool(getattr(bound_tenant(), "is_demo", False))


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

    Read off the real row (``bound_tenant``), so a commit hook or task
    that entered its schema with ``schema_context`` is gated by the same
    plan as an HTTP request. A schema with no row, and the public
    schema, are not plan-gated.
    """
    # Public schema and a schema with no row -- platform routines are
    # not plan-gated.
    tenant = real_tenant(get_current_tenant())
    if tenant is None:
        return True
    return bool(getattr(tenant, flag, True))


_NO_MEMBERSHIP = object()


def get_membership(user: Any, tenant: Any | None = None) -> Any | None:
    """Return the active membership for user+tenant, or None.

    Remembered on the user object, which lives for one request: the
    admin site's ``has_permission`` runs several times per page, and the
    permission backend and ``is_store_staff`` ask again, so one admin
    page used to run the same SELECT four times. Keyed by tenant pk
    because one process serves many tenants and a user may hold a
    different role in each.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return None

    tenant = tenant or get_current_tenant()
    if tenant is None:
        return None

    cache_attr = f"_membership_{tenant.pk}"
    cached = getattr(user, cache_attr, None)
    if cached is not None:
        return None if cached is _NO_MEMBERSHIP else cached

    from tenant.models import UserTenantMembership

    membership = (
        UserTenantMembership.objects.filter(
            user=user,
            tenant=tenant,
            is_active=True,
        )
        .only("id", "role", "tenant_id", "user_id")
        .first()
    )
    setattr(
        user,
        cache_attr,
        _NO_MEMBERSHIP if membership is None else membership,
    )
    return membership


def is_platform_identity(user: Any) -> bool:
    """True when *user* provably came from the public schema.

    ``UserAccount`` is mirrored per schema and the cutover copied users
    id-preserving, so a TENANT-schema row is a customer record, and the
    privilege flags or permission grants it may still carry are residue.
    A user object is a platform identity in exactly two ways:

    - it carries ``PLATFORM_IDENTITY_ATTR``, stamped by
      ``PlatformStaffBackend`` (login and session restore) and by
      ``PlatformStaffTokenAuthentication`` — the only three places a
      platform identity is ever loaded; or
    - the connection is on the public schema, where
      ``user_useraccount`` IS the platform table. That is the platform
      control-plane host, and also the single-schema lane the main test
      suite runs in.

    A tenant-schema customer with a plain Knox token or session is
    neither.
    """
    if user is None:
        return False
    from tenant.auth_backends import PLATFORM_IDENTITY_ATTR

    if getattr(user, PLATFORM_IDENTITY_ATTR, False):
        return True
    return connection.schema_name == get_public_schema_name()


def is_platform_superuser(user: Any) -> bool:
    """True when *user*'s ``is_superuser`` flag can be believed.

    The flag alone cannot be: on a tenant-schema row it is residue on a
    CUSTOMER record — the identical argument this codebase already makes
    about ``is_staff``, on the identical rows. It is honoured only on a
    platform identity (``is_platform_identity``).
    """
    if user is None or not getattr(user, "is_superuser", False):
        return False
    return is_platform_identity(user)


def is_store_staff(user: Any) -> bool:
    """True when *user* may act as STAFF of the tenant on this connection.

    The API twin of ``admin.admin.MyAdminSite.has_permission``. It never
    reads ``is_staff``: on a tenant-schema row that flag is customer
    residue from the id-preserving cutover (see
    ``core.api.permissions.IsPlatformSuperuser``), so a customer carrying
    it must be indistinguishable from any other customer here.

    Who passes:

    - a platform superuser (parity with ``IsPlatformSuperuser`` and with
      ``has_perm``'s own short-circuit);
    - a user object stamped with ``PLATFORM_IDENTITY_ATTR`` — i.e. loaded
      from the PUBLIC schema by ``PlatformStaffBackend`` or
      ``PlatformStaffTokenAuthentication`` — holding an active,
      staff-capable membership in the CURRENT tenant.

    The membership answer is cached on the user object per tenant pk,
    exactly like ``TenantRolePermissionBackend``: one process serves
    many tenants and the same object must not carry one store's answer
    into a request for another.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if not getattr(user, "is_active", False):
        return False
    if is_platform_superuser(user):
        return True

    from tenant.auth_backends import PLATFORM_IDENTITY_ATTR

    if not getattr(user, PLATFORM_IDENTITY_ATTR, False):
        return False
    tenant = get_current_tenant()
    if tenant is None:
        return False

    cache_attr = f"_is_store_staff_{tenant.pk}"
    cached = getattr(user, cache_attr, None)
    if cached is not None:
        return cached

    membership = get_membership(user, tenant)
    result = membership is not None and membership.is_tenant_staff
    setattr(user, cache_attr, result)
    return result
