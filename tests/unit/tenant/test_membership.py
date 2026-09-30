"""Tests for tenant.membership helpers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from tenant.membership import (
    get_current_tenant,
    get_membership,
)
from tenant.models import (
    Tenant,
    TenantMembershipRole,
    UserTenantMembership,
)
from tests.utils.staff import store_tenant

User = get_user_model()


@pytest.fixture
def tenant(db) -> Tenant:
    return store_tenant("unit_membership_tenant")


@pytest.fixture
def user(db):
    return User.objects.create_user(
        username="alice-membership",
        email="alice-membership@example.com",
        password="p",
    )


class TestGetCurrentTenant:
    @pytest.mark.django_db
    def test_returns_tenant_when_schema_not_public(self, tenant, bind_tenant):
        bind_tenant(tenant)
        assert get_current_tenant() is tenant

    def test_returns_none_on_public_schema(self, bind_tenant):
        bind_tenant(SimpleNamespace(schema_name="public"))
        assert get_current_tenant() is None

    def test_returns_none_when_no_tenant(self, bind_tenant):
        bind_tenant(None)
        assert get_current_tenant() is None


class TestGetMembership:
    @pytest.mark.django_db
    def test_returns_active_membership(self, tenant, user, bind_tenant):
        bind_tenant(tenant)
        m = UserTenantMembership.objects.create(
            user=user,
            tenant=tenant,
            role=TenantMembershipRole.MEMBER,
        )
        got = get_membership(user)
        assert got is not None
        assert got.pk == m.pk

    @pytest.mark.django_db
    def test_returns_none_for_inactive_membership(
        self, tenant, user, bind_tenant
    ):
        bind_tenant(tenant)
        UserTenantMembership.objects.create(
            user=user,
            tenant=tenant,
            role=TenantMembershipRole.MEMBER,
            is_active=False,
        )
        assert get_membership(user) is None

    @pytest.mark.django_db
    def test_returns_none_when_user_missing(self, tenant, bind_tenant):
        bind_tenant(tenant)
        assert get_membership(None) is None

    @pytest.mark.django_db
    def test_returns_none_when_user_anonymous(self, tenant, bind_tenant):
        bind_tenant(tenant)
        anon = SimpleNamespace(is_authenticated=False)
        assert get_membership(anon) is None
