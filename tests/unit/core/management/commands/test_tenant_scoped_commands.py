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

    @pytest.mark.parametrize(
        ("is_active", "suspended_at"),
        [
            (False, timezone.now() - timedelta(hours=2)),
            (True, timezone.now() - timedelta(hours=2)),
        ],
        ids=["suspended", "active_with_leftover_suspended_at"],
    )
    def test_a_bare_run_on_public_refuses_while_any_store_exists(
        self, is_active, suspended_at
    ):
        """Store existence decides it, not whether the store is active:
        public's copies of the tenant tables serve no store."""
        from tests.utils.staff import store_tenant

        # The seeded stores go for this test (rolled back after it), so the
        # one below is the only store there is.
        Tenant.objects.stores().delete()
        store_tenant(
            "seed_guard_store",
            is_active=is_active,
            suspended_at=suspended_at,
        )

        with (
            schema_context("public"),
            pytest.raises(CommandError, match="seed_guard_store"),
        ):
            call_command("seed_all")
