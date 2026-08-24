"""Happy-path regression for ``tenant_create`` after it was refactored
to delegate its post-row provisioning to ``tenant/provisioning.py``
(shared with ``TenantAdmin.save_related`` — see H1).

The existing ``test_tenant_create_command.py`` only covers fast-fail
guards (reserved schema name, unknown plan) that never reach
provisioning. ``Tenant.create_schema`` is monkeypatched to a no-op —
same technique ``test_bootstrap_platform_command.py`` uses to spy on
it — so this stays a unit test (no real ``CREATE SCHEMA`` DDL / tenant
migrations) while still exercising the command's actual DB writes.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

from tenant.models import (
    Tenant,
    TenantMembershipRole,
    UserTenantMembership,
)

User = get_user_model()

pytestmark = pytest.mark.django_db


def test_happy_path_provisions_api_domain_and_owner_membership(monkeypatch):
    monkeypatch.setattr(Tenant, "create_schema", lambda self, *a, **kw: None)

    owner = User.objects.create_user(
        email="owner@happy-path-store.example.com",
        username="happypathowner",
        password="testpass123",
    )

    call_command(
        "tenant_create",
        name="Happy Path Store",
        slug="happy-path-store",
        schema_name="happy_path_store",
        domain="happy-path-store.example.com",
        owner_email="owner@happy-path-store.example.com",
    )

    tenant = Tenant.objects.get(schema_name="happy_path_store")

    from tenant.models import TenantDomain

    assert TenantDomain.objects.filter(
        tenant=tenant,
        domain="happy-path-store.example.com",
        is_primary=True,
    ).exists()
    assert TenantDomain.objects.filter(
        tenant=tenant,
        domain="api.happy-path-store.example.com",
        is_primary=False,
    ).exists()

    membership = UserTenantMembership.objects.get(tenant=tenant, user=owner)
    assert membership.role == TenantMembershipRole.OWNER
    assert membership.is_active is True


def test_extra_domains_are_still_created_alongside_the_derived_api_domain(
    monkeypatch,
):
    monkeypatch.setattr(Tenant, "create_schema", lambda self, *a, **kw: None)

    call_command(
        "tenant_create",
        name="Extra Domains Store",
        slug="extra-domains-store",
        schema_name="extra_domains_store",
        domain="extra-domains-store.example.com",
        owner_email="owner@extra-domains-store.example.com",
        extra_domains=["www.extra-domains-store.example.com"],
    )

    tenant = Tenant.objects.get(schema_name="extra_domains_store")

    from tenant.models import TenantDomain

    assert TenantDomain.objects.filter(
        tenant=tenant, domain="www.extra-domains-store.example.com"
    ).exists()
    assert TenantDomain.objects.filter(
        tenant=tenant, domain="api.extra-domains-store.example.com"
    ).exists()


def test_owner_not_registered_yet_does_not_fail_the_command(monkeypatch):
    """No UserAccount for owner_email — the command must finish
    successfully (CLI stdout warns; no membership is created)."""
    monkeypatch.setattr(Tenant, "create_schema", lambda self, *a, **kw: None)

    call_command(
        "tenant_create",
        name="No Owner Yet Store",
        slug="no-owner-yet-store",
        schema_name="no_owner_yet_store",
        domain="no-owner-yet-store.example.com",
        owner_email="not-registered@example.com",
    )

    tenant = Tenant.objects.get(schema_name="no_owner_yet_store")
    assert not UserTenantMembership.objects.filter(tenant=tenant).exists()
