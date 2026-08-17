"""Membership gating + per-tenant branding of ``MyAdminSite``.

``is_staff`` is a global flag on the shared ``UserAccount``; the admin
site must additionally require an active staff-capable membership in the
CURRENT tenant, or staff of store A could open store B's ``/admin/``.
Platform superusers pass everywhere by design (they manage all tenants
from the public-schema admin and may need to enter any tenant's).
"""

from __future__ import annotations

import pytest
from django.contrib.admin import site as admin_site
from django.db import connection
from django.test import RequestFactory

from tenant.models import (
    Tenant,
    TenantMembershipRole,
    UserTenantMembership,
)
from user.factories.account import UserAccountFactory


@pytest.fixture
def tenant(db):
    t = Tenant(
        schema_name="adminsite_tenant",
        name="Adminsite Tenant",
        slug="adminsite-tenant",
        owner_email="owner-adminsite@example.com",
        store_name="Adminsite Store",
    )
    t.auto_create_schema = False
    t.save()
    return t


@pytest.fixture
def bind_tenant(monkeypatch):
    def _bind(t):
        monkeypatch.setattr(connection, "tenant", t, raising=False)

    return _bind


def _request_for(user):
    request = RequestFactory().get("/admin/")
    request.user = user
    return request


@pytest.mark.django_db
class TestAdminSitePermission:
    def test_superuser_passes_on_any_tenant(self, tenant, bind_tenant):
        superuser = UserAccountFactory(is_staff=True, is_superuser=True)
        bind_tenant(tenant)
        assert admin_site.has_permission(_request_for(superuser)) is True

    def test_staff_passes_on_public_schema(self, db, monkeypatch):
        monkeypatch.setattr(connection, "tenant", None, raising=False)
        staff = UserAccountFactory(is_staff=True)
        assert admin_site.has_permission(_request_for(staff)) is True

    def test_staff_without_membership_denied_on_tenant(
        self, tenant, bind_tenant
    ):
        # The cross-tenant case: is_staff is global, membership is not.
        staff = UserAccountFactory(is_staff=True)
        bind_tenant(tenant)
        assert admin_site.has_permission(_request_for(staff)) is False

    def test_member_role_denied_on_tenant(self, tenant, bind_tenant):
        staff = UserAccountFactory(is_staff=True)
        UserTenantMembership.objects.create(
            user=staff,
            tenant=tenant,
            role=TenantMembershipRole.MEMBER,
            is_active=True,
        )
        bind_tenant(tenant)
        assert admin_site.has_permission(_request_for(staff)) is False

    def test_staff_role_allowed_on_tenant(self, tenant, bind_tenant):
        staff = UserAccountFactory(is_staff=True)
        UserTenantMembership.objects.create(
            user=staff,
            tenant=tenant,
            role=TenantMembershipRole.STAFF,
            is_active=True,
        )
        bind_tenant(tenant)
        assert admin_site.has_permission(_request_for(staff)) is True

    def test_inactive_membership_denied(self, tenant, bind_tenant):
        staff = UserAccountFactory(is_staff=True)
        UserTenantMembership.objects.create(
            user=staff,
            tenant=tenant,
            role=TenantMembershipRole.OWNER,
            is_active=False,
        )
        bind_tenant(tenant)
        assert admin_site.has_permission(_request_for(staff)) is False

    def test_non_staff_denied_even_with_membership(
        self, tenant, bind_tenant
    ):
        shopper = UserAccountFactory(is_staff=False)
        UserTenantMembership.objects.create(
            user=shopper,
            tenant=tenant,
            role=TenantMembershipRole.OWNER,
            is_active=True,
        )
        bind_tenant(tenant)
        # Django's base check (is_active AND is_staff) still applies.
        assert admin_site.has_permission(_request_for(shopper)) is False


@pytest.mark.django_db
class TestAdminSiteBranding:
    def test_tenant_host_shows_store_name(self, tenant, bind_tenant):
        superuser = UserAccountFactory(is_staff=True, is_superuser=True)
        bind_tenant(tenant)
        context = admin_site.each_context(_request_for(superuser))
        assert context["site_header"] == "Adminsite Store"
        assert "Adminsite Store" in str(context["site_title"])

    def test_public_schema_keeps_platform_branding(self, db, monkeypatch):
        monkeypatch.setattr(connection, "tenant", None, raising=False)
        superuser = UserAccountFactory(is_staff=True, is_superuser=True)
        context = admin_site.each_context(_request_for(superuser))
        assert context["site_header"] == admin_site.site_header
