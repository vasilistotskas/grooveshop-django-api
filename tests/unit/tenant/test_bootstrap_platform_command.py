"""Tests for the ``bootstrap_platform`` management command.

Creates/confirms the PUBLIC-schema platform tenant row + its primary
domain — the host platform staff use to reach the admin as platform
identities (``tenant.auth_backends.PlatformStaffBackend``).
"""

from __future__ import annotations

import pytest
from django.core.management import call_command
from django_tenants.utils import get_public_schema_name

from tenant.models import Tenant, TenantDomain

pytestmark = pytest.mark.django_db


def test_creates_public_tenant_and_domain():
    call_command("bootstrap_platform", domain="platform.example.com")

    tenant = Tenant.objects.get(schema_name=get_public_schema_name())
    assert tenant.is_active is True
    assert tenant.name == "Platform"

    domain = TenantDomain.objects.get(domain="platform.example.com")
    assert domain.tenant_id == tenant.pk
    assert domain.is_primary is True


def test_does_not_attempt_real_schema_creation(monkeypatch):
    """``auto_create_schema`` is set False on the instance before
    save() — the public schema always exists, there is nothing to
    CREATE. ``auto_create_schema`` is a plain Python attribute (not a
    model field), so it can't be asserted on a row re-fetched from the
    DB — spy on ``create_schema()`` (the DDL-issuing method) instead
    and assert it is never called.
    """
    calls = []
    original = Tenant.create_schema

    def _spy(self, *args, **kwargs):
        calls.append((args, kwargs))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Tenant, "create_schema", _spy)
    call_command("bootstrap_platform", domain="platform.example.com")
    assert calls == []


def test_rerun_with_same_domain_is_idempotent():
    call_command("bootstrap_platform", domain="platform.example.com")
    call_command("bootstrap_platform", domain="platform.example.com")

    assert (
        Tenant.objects.filter(schema_name=get_public_schema_name()).count() == 1
    )
    assert (
        TenantDomain.objects.filter(domain="platform.example.com").count() == 1
    )
    domain = TenantDomain.objects.get(domain="platform.example.com")
    assert domain.is_primary is True


def test_rerun_with_different_domain_becomes_primary_and_demotes_old():
    call_command("bootstrap_platform", domain="platform.example.com")
    call_command("bootstrap_platform", domain="platform-v2.example.com")

    old = TenantDomain.objects.get(domain="platform.example.com")
    new = TenantDomain.objects.get(domain="platform-v2.example.com")
    assert new.is_primary is True
    assert old.is_primary is False

    tenant = Tenant.objects.get(schema_name=get_public_schema_name())
    assert old.tenant_id == tenant.pk
    assert new.tenant_id == tenant.pk


def test_rerun_does_not_duplicate_tenant_row():
    call_command("bootstrap_platform", domain="a.example.com")
    call_command("bootstrap_platform", domain="b.example.com")
    call_command("bootstrap_platform", domain="c.example.com")

    assert (
        Tenant.objects.filter(schema_name=get_public_schema_name()).count() == 1
    )
