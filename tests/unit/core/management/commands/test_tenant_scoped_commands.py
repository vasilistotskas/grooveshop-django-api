"""Tenant scoping of operational management commands.

Commands whose data lives in TENANT_APPS tables must refuse to run from
the PUBLIC connection context without an explicit --tenant/--all-tenants
(on a fresh database the table does not even exist in public; on a
prod-clone it silently acts on pre-multi-tenant legacy rows). Callers
already inside a tenant context — the per-tenant Celery fanout tasks —
must keep working without flags.
"""

from datetime import timedelta

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone
from django_tenants.utils import schema_context

from notification.factories.notification import NotificationFactory
from notification.models.notification import Notification
from tenant.models import Tenant


def _ensure_public_tenant_row(slug: str) -> None:
    if Tenant.objects.filter(schema_name="public").exists():
        return
    tenant = Tenant(
        schema_name="public",
        name=f"Cmd Scope {slug}",
        slug=slug,
        owner_email=f"owner-{slug}@example.com",
    )
    tenant.auto_create_schema = False
    tenant.save()


@pytest.mark.django_db
class TestRequireTenantScopeGuard:
    def test_expire_notifications_refuses_public_context(self):
        with (
            schema_context("public"),
            pytest.raises(CommandError, match="public schema context"),
        ):
            call_command("expire_notifications")

    def test_reconcile_acs_cod_refuses_public_context(self):
        with (
            schema_context("public"),
            pytest.raises(CommandError, match="public schema context"),
        ):
            call_command("reconcile_acs_cod")

    def test_tenant_context_passes_without_flags(self):
        """The Celery fanout path: call_command inside a tenant context
        (TenantTask) must keep working flag-less."""
        expired = NotificationFactory(
            expiry_date=timezone.now() - timedelta(days=1)
        )
        with schema_context("cmd_scope_tenant"):
            call_command("expire_notifications")
        assert not Notification.objects.filter(pk=expired.pk).exists()

    def test_explicit_tenant_flag_targets_schema(self):
        _ensure_public_tenant_row("cmd-scope-flag")
        expired = NotificationFactory(
            expiry_date=timezone.now() - timedelta(days=1)
        )
        fresh = NotificationFactory(
            expiry_date=timezone.now() + timedelta(days=1)
        )

        call_command("expire_notifications", "--tenant", "public")

        assert not Notification.objects.filter(pk=expired.pk).exists()
        assert Notification.objects.filter(pk=fresh.pk).exists()

    def test_unknown_tenant_flag_rejected(self):
        with pytest.raises(CommandError, match="not found"):
            call_command("expire_notifications", "--tenant", "nope-nope")


@pytest.mark.django_db
class TestSeedAllSchemaFlag:
    def test_unknown_schema_rejected(self):
        with pytest.raises(CommandError, match="not found"):
            call_command("seed_all", "--schema", "nope-nope")
