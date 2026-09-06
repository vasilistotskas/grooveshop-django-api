"""Tests for the ``tenant_create`` management command's schema-name guard.

``Tenant.objects.create()`` inside the command bypasses ``full_clean()``
(plain ``.save()``, not a ModelForm/DRF serializer), so the field
validator on ``Tenant.schema_name`` never runs there — the command adds
its own explicit ``validate_reserved_schema_name`` check up front so a
reserved name fails fast with a clear ``CommandError`` instead of a
confusing downstream schema-creation error.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from tenant.models import Tenant

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
    from tenant.models import TenantDomain

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


def test_duplicate_domain_leaves_no_orphaned_tenant():
    """A failure mid-sequence must roll the whole tenant back.

    `Tenant.objects.create()` has `auto_create_schema=True`, so it
    creates the Postgres schema and replays every migration inline. The
    `TenantDomain.objects.create()` on the very next line is NOT a
    get_or_create and `domain` is unique — so a typo, or a domain left
    behind by an earlier failed run, raised `IntegrityError` against an
    already-committed tenant.

    That left a fully migrated schema with no domain, no owner and no
    seed data. And the command's own up-front guard then refused the
    obvious retry with the same schema name, so an operator had to clean
    up by hand before they could try again.
    """
    from django.db.utils import IntegrityError

    from tenant.models import TenantDomain

    taken = "already-taken.example.com"
    first = Tenant.objects.create(
        schema_name="first_store",
        name="First Store",
        slug="first-store",
        owner_email="owner@first-store.example.com",
    )
    TenantDomain.objects.create(domain=taken, tenant=first, is_primary=True)

    with pytest.raises(IntegrityError):
        call_command(
            "tenant_create",
            name="Second Store",
            slug="second-store",
            schema_name="second_store",
            domain=taken,
            owner_email="owner@second-store.example.com",
        )

    assert not Tenant.objects.filter(schema_name="second_store").exists(), (
        "the failed run left an orphaned tenant behind — and the "
        "command's own guard would then refuse to let it be retried"
    )
