"""Tests for tenant.models.UserTenantMembership."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError

from tenant.models import (
    Tenant,
    TenantDomain,
    TenantMembershipRole,
    UserTenantMembership,
)

User = get_user_model()


def _make_test_tenant(slug: str, schema_name: str) -> Tenant:
    """Create a Tenant row without hitting django-tenants' schema hook.

    django-tenants normally issues ``CREATE SCHEMA`` on save; in unit
    tests we only want the Python object + the ``tenant_tenant`` row —
    the DB router is disabled in conftest, so the schema never gets
    queried. Setting ``auto_create_schema=False`` on the instance
    overrides the class default so ``save()`` skips the hook.
    """
    tenant = Tenant(
        schema_name=schema_name,
        name=slug.replace("-", " ").title(),
        slug=slug,
        owner_email=f"owner-{slug}@example.com",
    )
    tenant.auto_create_schema = False
    tenant.save()
    return tenant


@pytest.fixture
def tenant(db) -> Tenant:
    """Isolated tenant row for each test.

    Unique schema_name + slug avoid collisions with the ``webside`` row
    seeded by migration 0002 and with other tests running in parallel.
    """
    tenant = _make_test_tenant(
        slug="unit-test-tenant-1",
        schema_name="unit_test_tenant_1",
    )
    TenantDomain.objects.create(
        tenant=tenant, domain="test-1.example.com", is_primary=True
    )
    return tenant


@pytest.fixture
def user(db):
    return User.objects.create_user(
        username="alice-unit",
        email="alice-unit@example.com",
        password="password",  # noqa: S106
    )


@pytest.mark.django_db
def test_membership_str_contains_user_tenant_and_role(tenant, user):
    membership = UserTenantMembership.objects.create(
        user=user, tenant=tenant, role=TenantMembershipRole.MEMBER
    )
    label = str(membership)
    assert "alice" in label or "alice@example.com" in label
    assert "Test Tenant" in label
    assert "member" in label


@pytest.mark.django_db
def test_membership_default_role_is_member(tenant, user):
    membership = UserTenantMembership.objects.create(user=user, tenant=tenant)
    assert membership.role == TenantMembershipRole.MEMBER
    assert membership.is_active is True


@pytest.mark.django_db
def test_can_manage_tenant_true_for_admin_and_owner(tenant, user):
    admin = UserTenantMembership(
        user=user, tenant=tenant, role=TenantMembershipRole.ADMIN
    )
    owner = UserTenantMembership(
        user=user, tenant=tenant, role=TenantMembershipRole.OWNER
    )
    member = UserTenantMembership(
        user=user, tenant=tenant, role=TenantMembershipRole.MEMBER
    )
    staff = UserTenantMembership(
        user=user, tenant=tenant, role=TenantMembershipRole.STAFF
    )
    assert admin.can_manage_tenant is True
    assert owner.can_manage_tenant is True
    assert staff.can_manage_tenant is False
    assert member.can_manage_tenant is False


@pytest.mark.django_db
def test_is_tenant_staff_true_for_staff_admin_owner(tenant, user):
    member = UserTenantMembership(
        user=user, tenant=tenant, role=TenantMembershipRole.MEMBER
    )
    staff = UserTenantMembership(
        user=user, tenant=tenant, role=TenantMembershipRole.STAFF
    )
    admin = UserTenantMembership(
        user=user, tenant=tenant, role=TenantMembershipRole.ADMIN
    )
    owner = UserTenantMembership(
        user=user, tenant=tenant, role=TenantMembershipRole.OWNER
    )
    assert member.is_tenant_staff is False
    assert staff.is_tenant_staff is True
    assert admin.is_tenant_staff is True
    assert owner.is_tenant_staff is True


@pytest.mark.django_db
def test_unique_user_tenant_constraint(tenant, user):
    UserTenantMembership.objects.create(user=user, tenant=tenant)
    with pytest.raises(IntegrityError):
        UserTenantMembership.objects.create(
            user=user, tenant=tenant, role=TenantMembershipRole.ADMIN
        )


@pytest.mark.django_db
def test_same_user_can_belong_to_two_tenants(user):
    tenant_a = _make_test_tenant("unit-two-a", "unit_two_a")
    tenant_b = _make_test_tenant("unit-two-b", "unit_two_b")
    m_a = UserTenantMembership.objects.create(
        user=user, tenant=tenant_a, role=TenantMembershipRole.MEMBER
    )
    m_b = UserTenantMembership.objects.create(
        user=user, tenant=tenant_b, role=TenantMembershipRole.ADMIN
    )
    assert m_a.tenant_id != m_b.tenant_id
    assert user.tenant_memberships.count() == 2
