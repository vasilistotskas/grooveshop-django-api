"""Role-derived model permissions for the tenant admin.

``UserTenantMembership`` always described what each role may do, but
nothing mapped those roles onto Django permissions — ``tenant_create``
issues a membership and no Group exists anywhere in the codebase. The
membership gate therefore admitted a store operator to ``/admin/`` while
every model page answered 403, so the tenant admin was usable only by
platform superusers (who bypass permission checks entirely). Reported
from production 2026-08-21 by tenant #1's owner.

The security property these tests exist to hold: a role grants STORE
models and never PLATFORM ones, and it grants only to an identity that
came out of the public schema.
"""

from __future__ import annotations

import pytest
from django.db import connection

from tenant.auth_backends import (
    PLATFORM_IDENTITY_ATTR,
    TenantRolePermissionBackend,
)
from tenant.models import (
    Tenant,
    TenantMembershipRole,
    UserTenantMembership,
)
from user.factories.account import UserAccountFactory


@pytest.fixture
def tenant(db):
    t = Tenant(
        schema_name="roleperm_tenant",
        name="Roleperm Tenant",
        slug="roleperm-tenant",
        owner_email="owner-roleperm@example.com",
        store_name="Roleperm Store",
    )
    t.auto_create_schema = False
    t.save()
    return t


@pytest.fixture
def other_tenant(db):
    t = Tenant(
        schema_name="roleperm_other",
        name="Roleperm Other",
        slug="roleperm-other",
        owner_email="owner-roleperm-other@example.com",
        store_name="Roleperm Other Store",
    )
    t.auto_create_schema = False
    t.save()
    return t


@pytest.fixture
def bind_tenant(monkeypatch):
    def _bind(t):
        monkeypatch.setattr(connection, "tenant", t, raising=False)

    return _bind


def _staff_identity(**kwargs):
    """A public-schema staff user, stamped as PlatformStaffBackend would."""
    user = UserAccountFactory(is_staff=True, **kwargs)
    setattr(user, PLATFORM_IDENTITY_ATTR, True)
    return user


def _member(user, tenant, role, *, is_active=True):
    return UserTenantMembership.objects.create(
        user=user, tenant=tenant, role=role, is_active=is_active
    )


# Store models the role layer must reach, and platform models it must
# never reach regardless of role.
STORE_PERMS = (
    "order.view_order",
    "product.change_product",
    "blog.add_blogpost",
)
PLATFORM_PERMS = (
    "tenant.add_tenant",
    "tenant.delete_tenant",
    "tenant.change_tenantdomain",
    "auth.add_group",
    "auth.change_permission",
    "country.change_country",
    "region.change_region",
    "django_celery_beat.change_periodictask",
    "sites.change_site",
)


@pytest.mark.django_db
class TestOwnerAndAdmin:
    @pytest.mark.parametrize("role", ["owner", "admin"])
    @pytest.mark.parametrize("perm", STORE_PERMS)
    def test_store_models_are_granted(self, tenant, bind_tenant, role, perm):
        user = _staff_identity()
        _member(user, tenant, role)
        bind_tenant(tenant)
        assert TenantRolePermissionBackend().has_perm(user, perm)

    @pytest.mark.parametrize("role", ["owner", "admin"])
    @pytest.mark.parametrize("perm", PLATFORM_PERMS)
    def test_platform_models_are_never_granted(
        self, tenant, bind_tenant, role, perm
    ):
        user = _staff_identity()
        _member(user, tenant, role)
        bind_tenant(tenant)
        assert not TenantRolePermissionBackend().has_perm(user, perm), (
            f"{role} was granted platform permission {perm}"
        )

    @pytest.mark.parametrize("role", ["owner", "admin"])
    def test_may_edit_their_own_store_row(self, tenant, bind_tenant, role):
        """Narrow exception: the store's own Tenant row.

        Object scoping (own row only) is the ModelAdmin's job; this
        layer only makes the page reachable.
        """
        user = _staff_identity()
        _member(user, tenant, role)
        bind_tenant(tenant)
        backend = TenantRolePermissionBackend()
        assert backend.has_perm(user, "tenant.change_tenant")
        assert not backend.has_perm(user, "tenant.add_tenant")
        assert not backend.has_perm(user, "tenant.delete_tenant")

    @pytest.mark.parametrize("role", ["owner", "admin"])
    def test_may_manage_their_team(self, tenant, bind_tenant, role):
        user = _staff_identity()
        _member(user, tenant, role)
        bind_tenant(tenant)
        backend = TenantRolePermissionBackend()
        assert backend.has_perm(user, "tenant.add_usertenantmembership")
        assert backend.has_perm(user, "tenant.change_usertenantmembership")


@pytest.mark.django_db
class TestStaffRole:
    def test_gets_the_operational_surface(self, tenant, bind_tenant):
        user = _staff_identity()
        _member(user, tenant, TenantMembershipRole.STAFF)
        bind_tenant(tenant)
        backend = TenantRolePermissionBackend()
        assert backend.has_perm(user, "order.view_order")
        assert backend.has_perm(user, "order.change_order")

    def test_cannot_change_store_settings(self, tenant, bind_tenant):
        """ "cannot change tenant settings" (TenantMembershipRole)."""
        user = _staff_identity()
        _member(user, tenant, TenantMembershipRole.STAFF)
        bind_tenant(tenant)
        backend = TenantRolePermissionBackend()
        assert not backend.has_perm(user, "extra_settings.change_setting")
        assert not backend.has_perm(user, "tenant.change_tenant")

    def test_cannot_invite_staff(self, tenant, bind_tenant):
        """ "cannot ... invite other staff" (TenantMembershipRole)."""
        user = _staff_identity()
        _member(user, tenant, TenantMembershipRole.STAFF)
        bind_tenant(tenant)
        backend = TenantRolePermissionBackend()
        assert not backend.has_perm(user, "tenant.add_usertenantmembership")

    def test_cannot_delete(self, tenant, bind_tenant):
        """Delete is the irreversible action; the role text implies none."""
        user = _staff_identity()
        _member(user, tenant, TenantMembershipRole.STAFF)
        bind_tenant(tenant)
        assert not TenantRolePermissionBackend().has_perm(
            user, "order.delete_order"
        )


@pytest.mark.django_db
class TestGrantsNothing:
    def test_without_a_membership(self, tenant, bind_tenant):
        user = _staff_identity()
        bind_tenant(tenant)
        assert TenantRolePermissionBackend().get_all_permissions(user) == set()

    def test_for_an_inactive_membership(self, tenant, bind_tenant):
        user = _staff_identity()
        _member(user, tenant, TenantMembershipRole.OWNER, is_active=False)
        bind_tenant(tenant)
        assert TenantRolePermissionBackend().get_all_permissions(user) == set()

    def test_for_the_retired_member_role(self, tenant, bind_tenant):
        user = _staff_identity()
        _member(user, tenant, TenantMembershipRole.MEMBER)
        bind_tenant(tenant)
        assert TenantRolePermissionBackend().get_all_permissions(user) == set()

    def test_for_an_inactive_user(self, tenant, bind_tenant):
        user = _staff_identity(is_active=False)
        _member(user, tenant, TenantMembershipRole.OWNER)
        bind_tenant(tenant)
        assert TenantRolePermissionBackend().get_all_permissions(user) == set()

    def test_on_the_public_schema(self, tenant, bind_tenant):
        """No current tenant means no role to derive anything from."""
        user = _staff_identity()
        _member(user, tenant, TenantMembershipRole.OWNER)
        bind_tenant(None)
        assert TenantRolePermissionBackend().get_all_permissions(user) == set()

    def test_for_an_unstamped_identity(self, tenant, bind_tenant):
        """THE collision defence.

        ``UserAccount`` is mirrored per schema, so a tenant-schema
        CUSTOMER can share a pk (or an email) with a public-schema staff
        identity. Only provenance is a sound signal, so an object that
        ``PlatformStaffBackend`` did not load grants nothing — even with
        a real OWNER membership row pointing at that pk.
        """
        user = UserAccountFactory(is_staff=True)  # deliberately unstamped
        _member(user, tenant, TenantMembershipRole.OWNER)
        bind_tenant(tenant)
        assert TenantRolePermissionBackend().get_all_permissions(user) == set()

    def test_object_level_checks_are_not_answered(self, tenant, bind_tenant):
        user = _staff_identity()
        _member(user, tenant, TenantMembershipRole.OWNER)
        bind_tenant(tenant)
        assert (
            TenantRolePermissionBackend().get_all_permissions(user, obj=tenant)
            == set()
        )


@pytest.mark.django_db
class TestTenantIsolation:
    def test_a_role_does_not_leak_into_another_tenant(
        self, tenant, other_tenant, bind_tenant
    ):
        """Owner of store A must hold nothing in store B."""
        user = _staff_identity()
        _member(user, tenant, TenantMembershipRole.OWNER)
        backend = TenantRolePermissionBackend()

        bind_tenant(tenant)
        assert backend.has_perm(user, "order.view_order")

        bind_tenant(other_tenant)
        assert not backend.has_perm(user, "order.view_order")

    def test_the_cache_is_keyed_per_tenant(
        self, tenant, other_tenant, bind_tenant
    ):
        """A single user object serves both stores within a process.

        An unkeyed cache would hand store A's answer to store B.
        """
        user = _staff_identity()
        _member(user, tenant, TenantMembershipRole.OWNER)
        _member(user, other_tenant, TenantMembershipRole.STAFF)
        backend = TenantRolePermissionBackend()

        bind_tenant(tenant)
        assert backend.has_perm(user, "extra_settings.change_setting")

        bind_tenant(other_tenant)
        assert not backend.has_perm(user, "extra_settings.change_setting")


@pytest.mark.django_db
class TestModulePerms:
    def test_reports_modules_the_role_can_reach(self, tenant, bind_tenant):
        user = _staff_identity()
        _member(user, tenant, TenantMembershipRole.OWNER)
        bind_tenant(tenant)
        backend = TenantRolePermissionBackend()
        assert backend.has_module_perms(user, "order")
        assert not backend.has_module_perms(user, "country")

    def test_module_perms_follow_the_role(self, tenant, bind_tenant):
        user = _staff_identity()
        _member(user, tenant, TenantMembershipRole.STAFF)
        bind_tenant(tenant)
        backend = TenantRolePermissionBackend()
        assert backend.has_module_perms(user, "order")
        assert not backend.has_module_perms(user, "extra_settings")


@pytest.mark.django_db
class TestThroughDjangosPermissionChain:
    """The backend must work via ``user.has_perm``, not just directly.

    ``_user_has_perm`` iterates every configured backend, so this proves
    the wiring in AUTHENTICATION_BACKENDS is live.
    """

    def test_owner_passes_a_real_has_perm_call(self, tenant, bind_tenant):
        user = _staff_identity()
        _member(user, tenant, TenantMembershipRole.OWNER)
        bind_tenant(tenant)
        assert user.has_perm("order.view_order")
        assert not user.has_perm("country.change_country")

    def test_backend_is_registered(self):
        from django.conf import settings

        assert (
            "tenant.auth_backends.TenantRolePermissionBackend"
            in settings.AUTHENTICATION_BACKENDS
        )


@pytest.mark.django_db
class TestPlatformIdentityStamp:
    def test_get_user_stamps_the_identity(self):
        """The stamp is what the whole provenance gate rests on."""
        from tenant.auth_backends import PlatformStaffBackend

        user = UserAccountFactory(is_staff=True)
        loaded = PlatformStaffBackend().get_user(user.pk)
        assert loaded is not None
        assert getattr(loaded, PLATFORM_IDENTITY_ATTR, False) is True
