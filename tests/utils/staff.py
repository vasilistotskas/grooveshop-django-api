"""Helpers for tests that need a real STORE STAFF identity.

``is_store_staff`` (``tenant.membership``) grants only to a user object
that carries the platform provenance stamp AND holds a staff-capable
membership in the tenant bound to the connection. The main test lane
strips ``TenantMainMiddleware``, so tests bind the tenant themselves
(``bind_tenant`` fixture in ``tests/conftest.py`` or
``connection.tenant = ...`` in ``setUp``) and build the identity here.
"""

from __future__ import annotations

from django.db import connection

from tenant.auth_backends import PLATFORM_IDENTITY_ATTR
from tenant.models import (
    Tenant,
    TenantMembershipRole,
    UserTenantMembership,
)
from user.factories.account import UserAccountFactory


def store_tenant(schema_name: str, **kwargs) -> Tenant:
    """Persist a ``Tenant`` row without creating a Postgres schema."""
    slug = schema_name.replace("_", "-")
    tenant = Tenant(
        schema_name=schema_name,
        name=kwargs.pop("name", slug),
        slug=kwargs.pop("slug", slug),
        owner_email=kwargs.pop("owner_email", f"owner-{slug}@example.com"),
        store_name=kwargs.pop("store_name", f"{slug} store"),
        **kwargs,
    )
    tenant.auto_create_schema = False
    tenant.save()
    return tenant


def bind_store_tenant(tenant: Tenant | None):
    """Bind *tenant* to the connection; returns the state to restore.

    Goes through the django-tenants API rather than assigning
    ``connection.tenant`` directly: any code path that re-enters
    ``schema_context`` (order signals do) restores the connection with
    ``set_tenant(previous)`` on exit, which also rewrites
    ``connection.schema_name``. Restoring only the attribute afterwards
    would leave the schema name pointing at the test tenant, and the
    next ``Tenant.save()`` in the process would refuse to run outside
    the public schema. Pass the return value to ``unbind_store_tenant``.
    """
    previous = getattr(connection, "tenant", None)
    if tenant is None:
        connection.set_schema_to_public()
    else:
        connection.set_tenant(tenant)
    return previous


def unbind_store_tenant(previous) -> None:
    if previous is None:
        connection.set_schema_to_public()
    else:
        connection.set_tenant(previous)


def stamp_platform_identity(user):
    """Mark *user* as loaded from the public schema, as the platform
    staff backend and the staff token authentication do."""
    setattr(user, PLATFORM_IDENTITY_ATTR, True)
    return user


def store_staff(
    tenant: Tenant,
    role: str = TenantMembershipRole.STAFF,
    **user_kwargs,
):
    """A stamped platform identity with an active *role* in *tenant*.

    ``APIClient.force_authenticate(user=...)`` keeps the stamped object,
    so the identity survives into ``request.user``.
    """
    user = UserAccountFactory(is_staff=True, **user_kwargs)
    UserTenantMembership.objects.create(
        user=user, tenant=tenant, role=role, is_active=True
    )
    return stamp_platform_identity(user)
