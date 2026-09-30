"""Object-scoping for a store operator's own admin.

``TenantRolePermissionBackend`` decides that a store ADMIN/OWNER may
reach ``Tenant`` and ``UserTenantMembership`` at all; these tests cover
the other half — that they reach only THEIR row, their own team, and
only the fields that are theirs to set.

Without this layer ``change_tenant`` would be a licence to edit every
merchant's payment credentials, since a permission in Django is granted
per MODEL, not per object.
"""

from __future__ import annotations

import pytest
from django.contrib.admin import site as admin_site
from django.test import RequestFactory

from tenant.auth_backends import PLATFORM_IDENTITY_ATTR
from tenant.models import (
    Tenant,
    TenantMembershipRole,
    UserTenantMembership,
)
from tests.utils.staff import store_tenant
from user.factories.account import UserAccountFactory
from user.models import UserAccount


@pytest.fixture
def tenant(db):
    return store_tenant("selfservice_tenant", store_name="Selfservice Store")


@pytest.fixture
def other_tenant(db):
    return store_tenant("selfservice_other")


def _request(user):
    request = RequestFactory().get("/admin/")
    request.user = user
    return request


def _store_operator(tenant, role=TenantMembershipRole.OWNER):
    user = UserAccountFactory(is_staff=True)
    setattr(user, PLATFORM_IDENTITY_ATTR, True)
    UserTenantMembership.objects.create(user=user, tenant=tenant, role=role)
    return user


def _superuser():
    user = UserAccountFactory(is_staff=True, is_superuser=True)
    setattr(user, PLATFORM_IDENTITY_ATTR, True)
    return user


@pytest.mark.django_db
class TestTenantAdminScoping:
    def _admin(self):
        return admin_site._registry[Tenant]

    def test_operator_sees_only_their_own_store(
        self, tenant, other_tenant, bind_tenant_and_schema
    ):
        user = _store_operator(tenant)
        bind_tenant_and_schema(tenant)
        qs = self._admin().get_queryset(_request(user))
        assert list(qs.values_list("pk", flat=True)) == [tenant.pk]

    def test_superuser_sees_every_store(
        self, tenant, other_tenant, bind_tenant_and_schema
    ):
        bind_tenant_and_schema(tenant)
        qs = self._admin().get_queryset(_request(_superuser()))
        pks = set(qs.values_list("pk", flat=True))
        assert {tenant.pk, other_tenant.pk} <= pks

    def test_platform_fields_are_read_only(
        self, tenant, bind_tenant_and_schema
    ):
        user = _store_operator(tenant)
        bind_tenant_and_schema(tenant)
        readonly = set(self._admin().get_readonly_fields(_request(user)))
        for platform_field in (
            "schema_name",
            "slug",
            "plan",
            "paid_until",
            "is_active",
            "suspended_at",
            "allowed_csp_sources",
            "blog_enabled",
            "loyalty_enabled",
        ):
            assert platform_field in readonly, (
                f"{platform_field} is editable by a merchant"
            )

    def test_their_own_settings_stay_editable(
        self, tenant, bind_tenant_and_schema
    ):
        user = _store_operator(tenant)
        bind_tenant_and_schema(tenant)
        readonly = set(self._admin().get_readonly_fields(_request(user)))
        for own_field in (
            "store_name",
            "primary_color",
            "from_email",
            "viva_wallet_client_id",
            "acs_api_key",
        ):
            assert own_field not in readonly, (
                f"{own_field} is their own to set but was read-only"
            )

    def test_cannot_create_or_delete_stores(
        self, tenant, bind_tenant_and_schema
    ):
        user = _store_operator(tenant)
        bind_tenant_and_schema(tenant)
        request = _request(user)
        assert self._admin().has_add_permission(request) is False
        assert self._admin().has_delete_permission(request) is False

    def test_domains_are_not_offered(self, tenant, bind_tenant_and_schema):
        """Domains steer routing and TLS — platform-controlled."""
        user = _store_operator(tenant)
        bind_tenant_and_schema(tenant)
        assert self._admin().get_inlines(_request(user), None) == []

    def test_lifecycle_actions_are_withheld(
        self, tenant, bind_tenant_and_schema
    ):
        """A merchant must not be able to suspend or destroy their store."""
        user = _store_operator(tenant)
        bind_tenant_and_schema(tenant)
        actions = self._admin().get_actions(_request(user))
        for name in (
            "suspend_tenants",
            "activate_tenants",
            "destroy_tenants",
            "delete_selected",
        ):
            assert name not in actions

    def test_superuser_keeps_lifecycle_actions(
        self, tenant, bind_tenant_and_schema
    ):
        bind_tenant_and_schema(tenant)
        actions = self._admin().get_actions(_request(_superuser()))
        assert "suspend_tenants" in actions
        assert "destroy_tenants" in actions

    def test_module_is_reachable_on_a_tenant_host(
        self, tenant, bind_tenant_and_schema
    ):
        """Previously public-only, which made self-service impossible."""
        user = _store_operator(tenant)
        bind_tenant_and_schema(tenant)
        assert self._admin().has_module_permission(_request(user)) is True

    def test_module_is_hidden_from_staff_role(
        self, tenant, bind_tenant_and_schema
    ):
        user = _store_operator(tenant, TenantMembershipRole.STAFF)
        bind_tenant_and_schema(tenant)
        assert not self._admin().has_module_permission(_request(user))


@pytest.mark.django_db
class TestMembershipAdminScoping:
    def _admin(self):
        return admin_site._registry[UserTenantMembership]

    def test_operator_sees_only_their_own_team(
        self, tenant, other_tenant, bind_tenant_and_schema
    ):
        user = _store_operator(tenant)
        stranger = UserAccountFactory(is_staff=True)
        UserTenantMembership.objects.create(
            user=stranger,
            tenant=other_tenant,
            role=TenantMembershipRole.OWNER,
        )
        bind_tenant_and_schema(tenant)
        qs = self._admin().get_queryset(_request(user))
        assert set(qs.values_list("tenant_id", flat=True)) == {tenant.pk}

    def test_admin_may_reach_the_team_page(
        self, tenant, bind_tenant_and_schema
    ):
        user = _store_operator(tenant, TenantMembershipRole.ADMIN)
        bind_tenant_and_schema(tenant)
        assert self._admin().has_module_permission(_request(user)) is True

    def test_staff_may_not_reach_the_team_page(
        self, tenant, bind_tenant_and_schema
    ):
        """ "cannot ... invite other staff" (TenantMembershipRole)."""
        user = _store_operator(tenant, TenantMembershipRole.STAFF)
        bind_tenant_and_schema(tenant)
        assert not self._admin().has_module_permission(_request(user))

    def test_an_admin_cannot_edit_the_owner(
        self, tenant, bind_tenant_and_schema
    ):
        """ "OWNER ... cannot be demoted/removed by other admins"."""
        admin_user = _store_operator(tenant, TenantMembershipRole.ADMIN)
        owner_row = UserTenantMembership.objects.create(
            user=UserAccountFactory(is_staff=True),
            tenant=tenant,
            role=TenantMembershipRole.OWNER,
        )
        bind_tenant_and_schema(tenant)
        request = _request(admin_user)
        assert not self._admin().has_change_permission(request, owner_row)
        assert not self._admin().has_delete_permission(request, owner_row)

    def test_an_owner_may_edit_an_owner(self, tenant, bind_tenant_and_schema):
        owner = _store_operator(tenant, TenantMembershipRole.OWNER)
        other_owner = UserTenantMembership.objects.create(
            user=UserAccountFactory(is_staff=True),
            tenant=tenant,
            role=TenantMembershipRole.OWNER,
        )
        bind_tenant_and_schema(tenant)
        assert self._admin().has_change_permission(_request(owner), other_owner)

    def test_an_admin_cannot_mint_an_owner(
        self, tenant, bind_tenant_and_schema
    ):
        """Otherwise ADMIN is indistinguishable from OWNER."""
        admin_user = _store_operator(tenant, TenantMembershipRole.ADMIN)
        bind_tenant_and_schema(tenant)
        field = UserTenantMembership._meta.get_field("role")
        formfield = self._admin().formfield_for_choice_field(
            field, _request(admin_user)
        )
        values = [value for value, _label in formfield.choices]
        assert TenantMembershipRole.OWNER not in values

    def test_cannot_grant_membership_in_another_store(
        self, tenant, other_tenant, bind_tenant_and_schema
    ):
        """The tenant dropdown is narrowed to their own store.

        Left unnarrowed, an ADMIN could save a membership row pointing
        at another merchant and walk into that store's admin.
        """
        user = _store_operator(tenant, TenantMembershipRole.ADMIN)
        bind_tenant_and_schema(tenant)
        db_field = UserTenantMembership._meta.get_field("tenant")
        formfield = self._admin().formfield_for_foreignkey(
            db_field, _request(user)
        )
        assert list(formfield.queryset.values_list("pk", flat=True)) == [
            tenant.pk
        ]

    def test_platform_console_keeps_every_store(
        self, tenant, other_tenant, bind_tenant_and_schema
    ):
        bind_tenant_and_schema(None)
        db_field = UserTenantMembership._meta.get_field("tenant")
        formfield = self._admin().formfield_for_foreignkey(
            db_field, _request(_superuser())
        )
        pks = set(formfield.queryset.values_list("pk", flat=True))
        assert {tenant.pk, other_tenant.pk} <= pks

    def test_an_owner_can_mint_an_owner(self, tenant, bind_tenant_and_schema):
        owner = _store_operator(tenant, TenantMembershipRole.OWNER)
        bind_tenant_and_schema(tenant)
        field = UserTenantMembership._meta.get_field("role")
        formfield = self._admin().formfield_for_choice_field(
            field, _request(owner)
        )
        values = [value for value, _label in formfield.choices]
        assert TenantMembershipRole.OWNER in values


@pytest.mark.django_db
class TestUserAdminPrivilegeFields:
    """``IsAdminUser`` is literally ``is_staff``.

    On a tenant host this admin edits that store's CUSTOMERS, so a store
    operator ticking ``is_staff`` would be granting API-admin rights.
    """

    def _admin(self):
        return admin_site._registry[UserAccount]

    def test_privilege_fields_are_read_only_for_non_superusers(
        self, tenant, bind_tenant_and_schema
    ):
        user = _store_operator(tenant)
        bind_tenant_and_schema(tenant)
        readonly = set(self._admin().get_readonly_fields(_request(user)))
        for field in ("is_staff", "is_superuser", "groups", "user_permissions"):
            assert field in readonly, f"{field} was editable by a merchant"

    def test_superuser_may_still_set_them(self, tenant, bind_tenant_and_schema):
        bind_tenant_and_schema(tenant)
        readonly = set(
            self._admin().get_readonly_fields(_request(_superuser()))
        )
        assert "is_staff" not in readonly
        assert "is_superuser" not in readonly
