"""Tests for the ``tenant_create`` management command.

``Tenant.objects.create()`` inside the command bypasses ``full_clean()``
(plain ``.save()``, not a ModelForm/DRF serializer), so the field
validator on ``Tenant.schema_name`` never runs there — the command adds
its own explicit ``validate_reserved_schema_name`` check up front so a
reserved name fails fast with a clear ``CommandError`` instead of a
confusing downstream schema-creation error.

The suite turns ``Tenant.auto_create_schema`` off, so the happy paths
exercise the command's own writes (domains, owner membership) without
``CREATE SCHEMA`` + migrate.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.db.utils import IntegrityError

from tenant.models import (
    Tenant,
    TenantDomain,
    TenantMembershipRole,
    UserTenantMembership,
)
from tests.utils.staff import store_tenant

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("reserved", ["public", "global", "information_schema"])
def test_reserved_schema_name_raises_command_error(reserved):
    with pytest.raises(CommandError, match="reserved schema name"):
        call_command(
            "tenant_create",
            name="Reserved Name Store",
            slug="reserved-name-store",
            schema_name=reserved,
            domain="reserved-name-store.example.com",
            owner_email="owner@reserved-name-store.example.com",
        )
    assert not Tenant.objects.filter(schema_name=reserved).exists()


def test_reserved_schema_name_check_runs_before_db_writes():
    """No TenantDomain / membership rows leak out when the schema name
    check fails — the guard must run before any side effect."""
    with pytest.raises(CommandError):
        call_command(
            "tenant_create",
            name="Blocked Store",
            slug="blocked-store",
            schema_name="pg_blocked",
            domain="blocked-store.example.com",
            owner_email="owner@blocked-store.example.com",
        )
    assert not TenantDomain.objects.filter(
        domain="blocked-store.example.com"
    ).exists()


def test_unknown_plan_is_rejected_via_cli_args():
    """Argparse ``choices`` guards the real CLI path."""
    with pytest.raises(CommandError):
        call_command(
            "tenant_create",
            "--name",
            "Bad Plan Store",
            "--slug",
            "bad-plan-store",
            "--schema",
            "bad_plan_store",
            "--domain",
            "bad-plan-store.example.com",
            "--owner-email",
            "owner@bad-plan-store.example.com",
            "--plan",
            "gold",
        )
    assert not Tenant.objects.filter(schema_name="bad_plan_store").exists()


def test_unknown_plan_is_rejected_via_call_command_kwargs():
    """``call_command`` only round-trips REQUIRED options through the
    parser, so keyword usage skips the argparse ``choices`` — the
    command re-asserts in ``handle()`` before any write."""
    with pytest.raises(CommandError, match="Invalid plan"):
        call_command(
            "tenant_create",
            name="Bad Plan Kw Store",
            slug="bad-plan-kw-store",
            schema_name="bad_plan_kw_store",
            domain="bad-plan-kw-store.example.com",
            owner_email="owner@bad-plan-kw-store.example.com",
            plan="gold",
        )
    assert not Tenant.objects.filter(schema_name="bad_plan_kw_store").exists()


def test_duplicate_domain_leaves_no_orphaned_tenant(monkeypatch):
    """A failure mid-sequence must roll the whole tenant back, schema
    included.

    ``Tenant.objects.create()`` creates the Postgres schema inline, and
    the ``TenantDomain.objects.create()`` right after it is not a
    get_or_create — so a duplicate domain (a typo, or one left by an
    earlier failed run) used to raise ``IntegrityError`` against an
    already-committed tenant and schema, which the command's own guard
    then refused to retry. Postgres DDL is transactional, so one
    ``atomic`` block undoes both.

    Schema creation is turned back on for this test with a bare
    ``CREATE SCHEMA`` in place of the per-tenant migrations.
    """
    taken = "already-taken.example.com"
    first = store_tenant("first_store")
    TenantDomain.objects.create(domain=taken, tenant=first, is_primary=True)

    created = []

    def _bare_schema(self, *args, **kwargs):
        with connection.cursor() as cursor:
            cursor.execute(f'CREATE SCHEMA "{self.schema_name}"')
        created.append(self.schema_name)

    monkeypatch.setattr(Tenant, "auto_create_schema", True)
    monkeypatch.setattr(Tenant, "create_schema", _bare_schema)

    with pytest.raises(IntegrityError):
        call_command(
            "tenant_create",
            name="Second Store",
            slug="second-store",
            schema_name="second_store",
            domain=taken,
            owner_email="owner@second-store.example.com",
        )

    assert created == ["second_store"]
    assert not Tenant.objects.filter(schema_name="second_store").exists()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s",
            ["second_store"],
        )
        assert cursor.fetchone() is None


def test_happy_path_provisions_api_domain_and_owner_membership():
    owner = get_user_model().objects.create_user(
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


def test_extra_domains_are_created_alongside_the_derived_api_domain():
    call_command(
        "tenant_create",
        name="Extra Domains Store",
        slug="extra-domains-store",
        schema_name="extra_domains_store",
        domain="extra-domains-store.example.com",
        owner_email="owner@extra-domains-store.example.com",
        extra_domains=["www.extra-domains-store.example.com"],
    )

    domains = set(
        TenantDomain.objects.filter(
            tenant__schema_name="extra_domains_store"
        ).values_list("domain", flat=True)
    )
    assert {
        "www.extra-domains-store.example.com",
        "api.extra-domains-store.example.com",
    } <= domains


def test_owner_not_registered_yet_does_not_fail_the_command():
    """No UserAccount for owner_email — the command finishes (CLI stdout
    warns) and no membership is created."""
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
