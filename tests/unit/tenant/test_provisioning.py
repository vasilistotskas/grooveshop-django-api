"""Unit tests for ``tenant/provisioning.py`` — the shared post-row
provisioning steps used by BOTH ``manage.py tenant_create`` and
``TenantAdmin.save_related`` (the "New Store" admin form).

Tenants here use ``auto_create_schema=False`` (via ``tenant_factory``)
so no real Postgres schema is created — ``seed_tenant_defaults``'s
``schema_context`` switch is still exercised (it does not require the
schema to physically exist; see ``admin/platform_dashboard.py``'s own
``_schema_exists`` comment for why a missing schema silently falls
through rather than erroring), and every step it runs is individually
best-effort/try-except'd already.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django_tenants.utils import schema_context

from tenant.models import (
    TenantDomain,
    TenantMembershipRole,
    UserTenantMembership,
)
from tenant.provisioning import (
    ensure_api_domain,
    provision_owner_membership,
    provision_tenant,
    seed_tenant_defaults,
)

User = get_user_model()

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# ensure_api_domain
# ---------------------------------------------------------------------------


class TestEnsureApiDomain:
    def test_creates_the_derived_api_domain(self, tenant_factory):
        tenant = tenant_factory("api-domain-store")
        TenantDomain.objects.create(
            domain="api-domain-store.example.com",
            tenant=tenant,
            is_primary=True,
        )

        result = ensure_api_domain(tenant)

        assert result == "api.api-domain-store.example.com"
        assert TenantDomain.objects.filter(
            tenant=tenant,
            domain="api.api-domain-store.example.com",
            is_primary=False,
        ).exists()

    def test_is_idempotent(self, tenant_factory):
        tenant = tenant_factory("api-domain-idempotent")
        TenantDomain.objects.create(
            domain="api-domain-idempotent.example.com",
            tenant=tenant,
            is_primary=True,
        )

        ensure_api_domain(tenant)
        ensure_api_domain(tenant)

        assert (
            TenantDomain.objects.filter(
                tenant=tenant,
                domain="api.api-domain-idempotent.example.com",
            ).count()
            == 1
        )

    def test_no_primary_domain_returns_none_and_creates_nothing(
        self, tenant_factory
    ):
        tenant = tenant_factory("api-domain-no-primary")

        result = ensure_api_domain(tenant)

        assert result is None
        assert not TenantDomain.objects.filter(tenant=tenant).exists()


# ---------------------------------------------------------------------------
# provision_owner_membership
# ---------------------------------------------------------------------------


class TestProvisionOwnerMembership:
    def test_creates_owner_membership_for_an_existing_user(
        self, tenant_factory
    ):
        tenant = tenant_factory("owner-membership-store")
        owner = User.objects.create_user(
            email="owner-membership@example.com",
            username="ownermembership",
            password="testpass123",
        )

        result = provision_owner_membership(tenant, owner.email)

        assert result is not None
        membership, created = result
        assert created is True
        assert membership.user_id == owner.pk
        assert membership.tenant_id == tenant.pk
        assert membership.role == TenantMembershipRole.OWNER
        assert membership.is_active is True

    def test_promotes_an_existing_membership_to_owner(self, tenant_factory):
        tenant = tenant_factory("owner-membership-promote")
        owner = User.objects.create_user(
            email="owner-promote@example.com",
            username="ownerpromote",
            password="testpass123",
        )
        UserTenantMembership.objects.create(
            user=owner,
            tenant=tenant,
            role=TenantMembershipRole.STAFF,
            is_active=True,
        )

        result = provision_owner_membership(tenant, owner.email)

        assert result is not None
        membership, created = result
        assert created is False
        assert membership.role == TenantMembershipRole.OWNER

    def test_missing_user_returns_none_and_creates_no_membership(
        self, tenant_factory
    ):
        tenant = tenant_factory("owner-membership-missing")

        result = provision_owner_membership(
            tenant, "nobody-registered@example.com"
        )

        assert result is None
        assert not UserTenantMembership.objects.filter(tenant=tenant).exists()

    def test_does_not_raise_when_called_from_inside_a_schema_context(
        self, tenant_factory
    ):
        """The function explicitly re-pins to the PUBLIC schema for its
        own lookup rather than trusting the ambient connection state
        (see the docstring) — calling it from inside an unrelated
        ``schema_context`` must not change the result."""
        tenant = tenant_factory("owner-membership-nested-context")
        owner = User.objects.create_user(
            email="owner-nested-context@example.com",
            username="ownernestedcontext",
            password="testpass123",
        )

        with schema_context(tenant.schema_name):
            result = provision_owner_membership(tenant, owner.email)

        assert result is not None
        membership, _created = result
        assert membership.user_id == owner.pk


# ---------------------------------------------------------------------------
# seed_tenant_defaults
# ---------------------------------------------------------------------------


class TestSeedTenantDefaults:
    def test_never_raises_even_without_a_real_schema(self, tenant_factory):
        """Every step inside is independently best-effort — a tenant
        whose Postgres schema was never created (``auto_create_schema
        =False``, as every test tenant here is) must not blow up
        provisioning."""
        tenant = tenant_factory("seed-defaults-no-schema")

        seed_tenant_defaults(tenant)  # must not raise

    def test_meilisearch_step_is_skipped_when_offline(
        self, tenant_factory, settings
    ):
        """``conftest.py`` sets ``MEILISEARCH["OFFLINE"] = True`` for
        the whole suite — confirm the seeding function honours it
        rather than attempting a real client call."""
        assert settings.MEILISEARCH.get("OFFLINE") is True
        tenant = tenant_factory("seed-defaults-offline")

        seed_tenant_defaults(tenant)  # must not raise / attempt a call


# ---------------------------------------------------------------------------
# provision_tenant (the aggregate the admin path calls)
# ---------------------------------------------------------------------------


class TestProvisionTenant:
    def test_runs_all_three_steps(self, tenant_factory):
        tenant = tenant_factory("provision-tenant-full")
        TenantDomain.objects.create(
            domain="provision-tenant-full.example.com",
            tenant=tenant,
            is_primary=True,
        )
        owner = User.objects.create_user(
            email=tenant.owner_email,
            username="provisiontenantfull",
            password="testpass123",
        )

        result = provision_tenant(tenant)

        assert result["api_domain"] == "api.provision-tenant-full.example.com"
        assert result["membership"] is not None
        membership, created = result["membership"]
        assert created is True
        assert membership.user_id == owner.pk

    def test_owner_email_defaults_to_the_tenant_field(self, tenant_factory):
        """``provision_tenant(tenant)`` with no explicit ``owner_email``
        must use ``tenant.owner_email`` — the admin "New Store" path
        never passes it explicitly."""
        tenant = tenant_factory("provision-tenant-default-owner")
        owner = User.objects.create_user(
            email=tenant.owner_email,
            username="provisiontenantdefaultowner",
            password="testpass123",
        )

        result = provision_tenant(tenant)

        assert result["membership"] is not None
        membership, _created = result["membership"]
        assert membership.user_id == owner.pk

    def test_missing_primary_domain_and_missing_owner_both_report_none(
        self, tenant_factory
    ):
        tenant = tenant_factory("provision-tenant-nothing")

        result = provision_tenant(tenant)

        assert result["api_domain"] is None
        assert result["membership"] is None

    def test_is_idempotent(self, tenant_factory):
        """Safe to re-run — e.g. after a partial failure — without
        creating duplicate domains or memberships."""
        tenant = tenant_factory("provision-tenant-idempotent")
        TenantDomain.objects.create(
            domain="provision-tenant-idempotent.example.com",
            tenant=tenant,
            is_primary=True,
        )
        User.objects.create_user(
            email=tenant.owner_email,
            username="provisiontenantidempotent",
            password="testpass123",
        )

        provision_tenant(tenant)
        provision_tenant(tenant)

        assert (
            TenantDomain.objects.filter(
                tenant=tenant,
                domain="api.provision-tenant-idempotent.example.com",
            ).count()
            == 1
        )
        assert UserTenantMembership.objects.filter(tenant=tenant).count() == 1
