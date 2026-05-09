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


# ---------------------------------------------------------------------------
# Tenant.clean() — meta_pixel_id validation
# ---------------------------------------------------------------------------


def test_meta_pixel_id_empty_is_valid():
    t = _unsaved_tenant(meta_pixel_id="")
    t.clean()  # must not raise


def test_meta_pixel_id_digits_only_is_valid():
    t = _unsaved_tenant(meta_pixel_id="123456789012345")
    t.clean()  # must not raise


def test_meta_pixel_id_non_digits_raises():
    t = _unsaved_tenant(meta_pixel_id="abc123")
    with pytest.raises(ValidationError) as exc_info:
        t.clean()
    assert "meta_pixel_id" in exc_info.value.message_dict


def test_meta_pixel_id_with_spaces_raises():
    t = _unsaved_tenant(meta_pixel_id="123 456")
    with pytest.raises(ValidationError) as exc_info:
        t.clean()
    assert "meta_pixel_id" in exc_info.value.message_dict


# ---------------------------------------------------------------------------
# Tenant.clean() — ga_tracking_id validation
# ---------------------------------------------------------------------------


def test_ga_tracking_id_empty_is_valid():
    t = _unsaved_tenant(ga_tracking_id="")
    t.clean()  # must not raise


def test_ga_tracking_id_ga4_prefix_is_valid():
    t = _unsaved_tenant(ga_tracking_id="G-ABCDE12345")
    t.clean()  # must not raise


def test_ga_tracking_id_ua_prefix_is_valid():
    t = _unsaved_tenant(ga_tracking_id="UA-123456-1")
    t.clean()  # must not raise


def test_ga_tracking_id_invalid_prefix_raises():
    t = _unsaved_tenant(ga_tracking_id="AW-1234567890")
    with pytest.raises(ValidationError) as exc_info:
        t.clean()
    assert "ga_tracking_id" in exc_info.value.message_dict


def test_ga_tracking_id_bare_string_raises():
    t = _unsaved_tenant(ga_tracking_id="not-a-ga-id")
    with pytest.raises(ValidationError) as exc_info:
        t.clean()
    assert "ga_tracking_id" in exc_info.value.message_dict


# ---------------------------------------------------------------------------
# Tenant.clean() — social URL validation (https-only)
# ---------------------------------------------------------------------------


def test_socials_facebook_https_is_valid():
    t = _unsaved_tenant(socials_facebook="https://facebook.com/myshop")
    t.clean()  # must not raise


def test_socials_facebook_empty_is_valid():
    t = _unsaved_tenant(socials_facebook="")
    t.clean()  # must not raise


def test_socials_facebook_http_raises():
    t = _unsaved_tenant(socials_facebook="http://facebook.com/myshop")
    with pytest.raises(ValidationError) as exc_info:
        t.clean()
    assert "socials_facebook" in exc_info.value.message_dict


def test_all_social_fields_https_are_valid():
    t = _unsaved_tenant(
        socials_discord="https://discord.gg/abc",
        socials_facebook="https://facebook.com/x",
        socials_instagram="https://instagram.com/x",
        socials_pinterest="https://pinterest.com/x",
        socials_reddit="https://reddit.com/r/x",
        socials_tiktok="https://tiktok.com/@x",
        socials_twitter="https://twitter.com/x",
        socials_youtube="https://youtube.com/c/x",
    )
    t.clean()  # must not raise


def test_multiple_social_http_raises_combined_error():
    """All bad social fields appear in message_dict."""
    t = _unsaved_tenant(
        socials_instagram="http://instagram.com/x",
        socials_twitter="http://twitter.com/x",
    )
    with pytest.raises(ValidationError) as exc_info:
        t.clean()
    errors = exc_info.value.message_dict
    assert "socials_instagram" in errors
    assert "socials_twitter" in errors


# ---------------------------------------------------------------------------
# Tenant.clean() — box_now_partner_id validation
# ---------------------------------------------------------------------------


def test_box_now_partner_id_empty_is_valid():
    t = _unsaved_tenant(box_now_partner_id="")
    t.clean()  # must not raise


def test_box_now_partner_id_digits_only_is_valid():
    t = _unsaved_tenant(box_now_partner_id="12345")
    t.clean()  # must not raise


def test_box_now_partner_id_non_digits_raises():
    t = _unsaved_tenant(box_now_partner_id="BN-12345")
    with pytest.raises(ValidationError) as exc_info:
        t.clean()
    assert "box_now_partner_id" in exc_info.value.message_dict
