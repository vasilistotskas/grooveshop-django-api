"""Tests for tenant.models.UserTenantMembership and Tenant validation."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
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


# ---------------------------------------------------------------------------
# Tenant.clean() — stripe_publishable_key validation
# ---------------------------------------------------------------------------


def _unsaved_tenant(**kwargs) -> Tenant:
    """Return an unsaved Tenant instance for clean() testing (no DB needed)."""
    defaults = dict(
        schema_name="clean_test",
        name="Clean Test",
        slug="clean-test",
        owner_email="owner@clean.example.com",
    )
    defaults.update(kwargs)
    t = Tenant(**defaults)
    t.auto_create_schema = False
    return t


def test_stripe_publishable_key_empty_is_valid():
    t = _unsaved_tenant(stripe_publishable_key="")
    t.clean()  # must not raise


def test_stripe_publishable_key_pk_live_is_valid():
    t = _unsaved_tenant(stripe_publishable_key="pk_live_abc123")
    t.clean()  # must not raise


def test_stripe_publishable_key_pk_test_is_valid():
    t = _unsaved_tenant(stripe_publishable_key="pk_test_abc123")
    t.clean()  # must not raise


def test_stripe_publishable_key_sk_live_raises():
    """Secret key must be rejected with a meaningful error."""
    t = _unsaved_tenant(stripe_publishable_key="sk_live_abc123")
    with pytest.raises(ValidationError) as exc_info:
        t.clean()
    assert "stripe_publishable_key" in exc_info.value.message_dict
    msg = " ".join(exc_info.value.message_dict["stripe_publishable_key"])
    assert "pk_test_" in msg or "pk_live_" in msg


def test_stripe_publishable_key_sk_test_raises():
    t = _unsaved_tenant(stripe_publishable_key="sk_test_abc123")
    with pytest.raises(ValidationError) as exc_info:
        t.clean()
    assert "stripe_publishable_key" in exc_info.value.message_dict


def test_stripe_publishable_key_random_string_raises():
    t = _unsaved_tenant(stripe_publishable_key="not_a_key")
    with pytest.raises(ValidationError):
        t.clean()


# ---------------------------------------------------------------------------
# Tenant.clean() — allowed_csp_sources validation
# ---------------------------------------------------------------------------


def test_allowed_csp_sources_empty_list_is_valid():
    t = _unsaved_tenant(allowed_csp_sources=[])
    t.clean()  # must not raise


def test_allowed_csp_sources_https_is_valid():
    t = _unsaved_tenant(
        allowed_csp_sources=["https://example.com", "https://cdn.example.com"]
    )
    t.clean()  # must not raise


def test_allowed_csp_sources_wss_is_valid():
    t = _unsaved_tenant(allowed_csp_sources=["wss://ws.example.com"])
    t.clean()  # must not raise


def test_allowed_csp_sources_localhost_http_is_valid():
    t = _unsaved_tenant(allowed_csp_sources=["http://localhost:3000"])
    t.clean()  # must not raise


def test_allowed_csp_sources_http_non_localhost_raises():
    t = _unsaved_tenant(allowed_csp_sources=["http://evil.example.com"])
    with pytest.raises(ValidationError) as exc_info:
        t.clean()
    assert "allowed_csp_sources" in exc_info.value.message_dict


def test_allowed_csp_sources_bare_domain_raises():
    t = _unsaved_tenant(allowed_csp_sources=["example.com"])
    with pytest.raises(ValidationError) as exc_info:
        t.clean()
    assert "allowed_csp_sources" in exc_info.value.message_dict


def test_allowed_csp_sources_non_string_entry_raises():
    t = _unsaved_tenant(allowed_csp_sources=[123])
    with pytest.raises(ValidationError) as exc_info:
        t.clean()
    assert "allowed_csp_sources" in exc_info.value.message_dict


def test_allowed_csp_sources_mixed_valid_invalid_raises():
    """A single invalid entry in an otherwise valid list must still fail."""
    t = _unsaved_tenant(
        allowed_csp_sources=["https://ok.com", "http://evil.com"]
    )
    with pytest.raises(ValidationError) as exc_info:
        t.clean()
    assert "allowed_csp_sources" in exc_info.value.message_dict
