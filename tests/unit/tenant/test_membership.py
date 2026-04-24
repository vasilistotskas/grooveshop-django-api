"""Tests for tenant.membership helpers + DRF permission."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.contrib.auth import get_user_model
from django.db import connection

from tenant.membership import (
    HasTenantAccess,
    get_current_tenant,
    get_membership,
    user_has_tenant_access,
)
from tenant.models import (
    Tenant,
    TenantMembershipRole,
    UserTenantMembership,
)

User = get_user_model()


@pytest.fixture
def tenant(db) -> Tenant:
    """Persisted tenant row with a non-public schema name.

    Uses a unit-test-scoped schema so django-tenants' unique constraint
    on ``schema_name`` doesn't collide with ``public`` or ``webside``.
    ``auto_create_schema=False`` on the instance skips the
    ``CREATE SCHEMA`` DDL — the conftest has already disabled the
    router so no query ever tries to use the schema.
    """
    t = Tenant(
        schema_name="unit_membership_tenant",
        name="Unit Test Tenant",
        slug="unit-membership-tenant",
        owner_email="owner@unit-test.example",
    )
    t.auto_create_schema = False
    t.save()
    return t


@pytest.fixture
def active_tenant(tenant):
    """Alias for ``tenant`` — the fixture already has a non-public
    schema so callers can treat it as an "active" tenant directly.
    """
    return tenant


@pytest.fixture
def user(db) -> User:
    return User.objects.create_user(
        username="alice-membership",
        email="alice-membership@example.com",
        password="p",  # noqa: S106
    )


@pytest.fixture
def bind_tenant(monkeypatch):
    """Attach a tenant to ``django.db.connection`` for a single test."""

    def _bind(t):
        monkeypatch.setattr(connection, "tenant", t, raising=False)

    yield _bind


class TestGetCurrentTenant:
    @pytest.mark.django_db
    def test_returns_tenant_when_schema_not_public(
        self, active_tenant, bind_tenant
    ):
        bind_tenant(active_tenant)
        assert get_current_tenant() is active_tenant

    def test_returns_none_on_public_schema(self, bind_tenant):
        bind_tenant(SimpleNamespace(schema_name="public"))
        assert get_current_tenant() is None

    def test_returns_none_when_no_tenant(self, bind_tenant):
        bind_tenant(None)
        assert get_current_tenant() is None


class TestGetMembership:
    @pytest.mark.django_db
    def test_returns_active_membership(self, active_tenant, user, bind_tenant):
        bind_tenant(active_tenant)
        m = UserTenantMembership.objects.create(
            user=user,
            tenant=active_tenant,
            role=TenantMembershipRole.MEMBER,
        )
        got = get_membership(user)
        assert got is not None
        assert got.pk == m.pk

    @pytest.mark.django_db
    def test_returns_none_for_inactive_membership(
        self, active_tenant, user, bind_tenant
    ):
        bind_tenant(active_tenant)
        UserTenantMembership.objects.create(
            user=user,
            tenant=active_tenant,
            role=TenantMembershipRole.MEMBER,
            is_active=False,
        )
        assert get_membership(user) is None

    @pytest.mark.django_db
    def test_returns_none_when_user_missing(self, active_tenant, bind_tenant):
        bind_tenant(active_tenant)
        assert get_membership(None) is None

    @pytest.mark.django_db
    def test_returns_none_when_user_anonymous(self, active_tenant, bind_tenant):
        bind_tenant(active_tenant)
        anon = SimpleNamespace(is_authenticated=False)
        assert get_membership(anon) is None


class TestUserHasTenantAccess:
    @pytest.mark.django_db
    def test_true_with_active_membership(
        self, active_tenant, user, bind_tenant
    ):
        bind_tenant(active_tenant)
        UserTenantMembership.objects.create(user=user, tenant=active_tenant)
        assert user_has_tenant_access(user) is True

    @pytest.mark.django_db
    def test_false_without_membership(self, active_tenant, user, bind_tenant):
        bind_tenant(active_tenant)
        assert user_has_tenant_access(user) is False

    @pytest.mark.django_db
    def test_platform_superuser_not_auto_granted(
        self, active_tenant, bind_tenant
    ):
        bind_tenant(active_tenant)
        superuser = User.objects.create_superuser(
            username="root-unit",
            email="root-unit@platform",
            password="p",  # noqa: S106
        )
        # Explicit — even superusers need a membership row; the helper
        # does not short-circuit on is_superuser.
        assert user_has_tenant_access(superuser) is False


class TestHasTenantAccessPermission:
    @pytest.mark.django_db
    def test_has_permission_delegates_to_helper(
        self, active_tenant, user, bind_tenant
    ):
        bind_tenant(active_tenant)
        UserTenantMembership.objects.create(user=user, tenant=active_tenant)

        permission = HasTenantAccess()
        request = MagicMock(user=user)
        assert permission.has_permission(request, view=MagicMock()) is True

    @pytest.mark.django_db
    def test_has_permission_rejects_without_membership(
        self, active_tenant, user, bind_tenant
    ):
        bind_tenant(active_tenant)
        permission = HasTenantAccess()
        request = MagicMock(user=user)
        assert permission.has_permission(request, view=MagicMock()) is False
