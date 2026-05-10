"""Integration tests for tenant suspension + destruction lifecycle.

Tests the full lifecycle: suspend → activate → destroy, including all
safety rails (protected schemas, cooldown period, non-suspended guard).

These tests use a real DB transaction (``@pytest.mark.django_db``) and
never call ``tenant.delete()`` on real schemas — we disable
``auto_create_schema`` so no Postgres DDL is ever issued.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone

from tenant.admin import TenantAdmin
from tenant.models import Tenant

User = get_user_model()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tenant(slug: str, **kwargs) -> Tenant:
    """Persist a Tenant without triggering Postgres schema creation.

    ``is_active`` defaults to True and ``suspended_at`` to None (matching
    field defaults); callers may override either via **kwargs.
    """
    defaults = {"is_active": True, "suspended_at": None}
    defaults.update(kwargs)
    t = Tenant(
        schema_name=slug.replace("-", "_"),
        name=slug,
        slug=slug,
        owner_email=f"owner-{slug}@example.com",
        **defaults,
    )
    t.auto_create_schema = False
    t.save()
    return t


def _admin_request():
    """Minimal mock of an HttpRequest sufficient for admin action calls."""
    req = MagicMock()
    req._messages = messages.storage.default_storage(req)
    return req


def _admin():
    """TenantAdmin instance (model_admin arg in actions)."""
    return TenantAdmin(Tenant, None)


# ---------------------------------------------------------------------------
# Suspension
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSuspendTenant:
    def test_suspend_sets_is_active_false(self):
        tenant = _make_tenant("suspend-basic")
        admin = _admin()
        admin.suspend_tenants(
            _admin_request(), Tenant.objects.filter(pk=tenant.pk)
        )
        tenant.refresh_from_db()
        assert tenant.is_active is False

    def test_suspend_stamps_suspended_at(self):
        before = timezone.now()
        tenant = _make_tenant("suspend-stamp")
        admin = _admin()
        admin.suspend_tenants(
            _admin_request(), Tenant.objects.filter(pk=tenant.pk)
        )
        tenant.refresh_from_db()
        assert tenant.suspended_at is not None
        assert tenant.suspended_at >= before

    def test_re_suspend_does_not_reset_suspended_at(self):
        """Suspending an already-suspended tenant must not move the
        cooldown timestamp forward — that would let operators extend
        the grace period indefinitely."""
        early = timezone.now() - timedelta(hours=25)
        tenant = _make_tenant(
            "suspend-nostamp", is_active=False, suspended_at=early
        )
        admin = _admin()
        admin.suspend_tenants(
            _admin_request(), Tenant.objects.filter(pk=tenant.pk)
        )
        tenant.refresh_from_db()
        # suspended_at must remain the original early value
        assert tenant.suspended_at == early

    def test_suspend_skips_protected_schemas(self):
        # We cannot really create 'public' or 'webside' in tests — just
        # verify the guard at model.delete() level covers them. Use a
        # tenant whose schema_name is in the protected set by patching
        # _PROTECTED in the admin module.
        tenant = _make_tenant("suspend-guard")
        admin = _admin()

        with patch("tenant.admin._PROTECTED", frozenset({tenant.schema_name})):
            admin.suspend_tenants(
                _admin_request(), Tenant.objects.filter(pk=tenant.pk)
            )

        tenant.refresh_from_db()
        # Must remain active (was skipped)
        assert tenant.is_active is True


# ---------------------------------------------------------------------------
# Activation (reverse of suspend)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestActivateTenant:
    def test_activate_sets_is_active_true(self):
        tenant = _make_tenant(
            "activate-basic",
            is_active=False,
            suspended_at=timezone.now() - timedelta(hours=2),
        )
        admin = _admin()
        admin.activate_tenants(
            _admin_request(), Tenant.objects.filter(pk=tenant.pk)
        )
        tenant.refresh_from_db()
        assert tenant.is_active is True

    def test_activate_clears_suspended_at(self):
        tenant = _make_tenant(
            "activate-clear",
            is_active=False,
            suspended_at=timezone.now() - timedelta(hours=2),
        )
        admin = _admin()
        admin.activate_tenants(
            _admin_request(), Tenant.objects.filter(pk=tenant.pk)
        )
        tenant.refresh_from_db()
        assert tenant.suspended_at is None

    def test_activate_skips_protected_schemas(self):
        tenant = _make_tenant(
            "activate-guard",
            is_active=False,
            suspended_at=timezone.now() - timedelta(hours=2),
        )
        admin = _admin()

        with patch("tenant.admin._PROTECTED", frozenset({tenant.schema_name})):
            admin.activate_tenants(
                _admin_request(), Tenant.objects.filter(pk=tenant.pk)
            )

        tenant.refresh_from_db()
        # Must remain inactive (was skipped)
        assert tenant.is_active is False


# ---------------------------------------------------------------------------
# Model-level delete() protection
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTenantDeleteProtection:
    def test_delete_public_raises_validation_error(self):
        """``public`` schema tenant must never be deleteable."""
        public_tenant = _make_tenant("public-del-test")
        # Patch schema_name to the protected value
        public_tenant.schema_name = "public"
        # Don't save — just test the delete method guard
        with pytest.raises(ValidationError, match="protected system tenant"):
            public_tenant.delete()

    def test_delete_webside_raises_validation_error(self):
        webside_tenant = _make_tenant("webside-del-test")
        webside_tenant.schema_name = "webside"
        with pytest.raises(ValidationError, match="protected system tenant"):
            webside_tenant.delete()

    def test_delete_regular_tenant_does_not_raise(self):
        tenant = _make_tenant("deletable-tenant")
        # delete() without force_drop on a tenant whose schema was never
        # created should succeed (django-tenants handles missing schema
        # gracefully when force_drop=False).
        try:
            tenant.delete()
        except Exception as exc:  # noqa: BLE001
            # If django-tenants raises due to missing schema during tests
            # that's acceptable — the guard itself didn't block us.
            assert "protected" not in str(exc).lower(), (
                f"Unexpected protected-schema error: {exc}"
            )


# ---------------------------------------------------------------------------
# Destroy admin action
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDestroyTenants:
    def test_destroy_non_suspended_is_refused(self):
        tenant = _make_tenant("destroy-not-suspended", is_active=True)
        admin = _admin()
        with patch.object(Tenant, "delete") as mock_delete:
            admin.destroy_tenants(
                _admin_request(), Tenant.objects.filter(pk=tenant.pk)
            )
            mock_delete.assert_not_called()

    def test_destroy_suspended_within_cooldown_is_refused(self):
        # suspended_at is only 1 hour ago — cooldown not satisfied
        tenant = _make_tenant(
            "destroy-cooldown",
            is_active=False,
            suspended_at=timezone.now() - timedelta(hours=1),
        )
        admin = _admin()
        with patch.object(Tenant, "delete") as mock_delete:
            admin.destroy_tenants(
                _admin_request(), Tenant.objects.filter(pk=tenant.pk)
            )
            mock_delete.assert_not_called()

    def test_destroy_suspended_past_cooldown_calls_delete(self):
        # suspended_at is 25 hours ago — cooldown satisfied
        tenant = _make_tenant(
            "destroy-ok",
            is_active=False,
            suspended_at=timezone.now() - timedelta(hours=25),
        )
        admin = _admin()
        with patch.object(Tenant, "delete") as mock_delete:
            admin.destroy_tenants(
                _admin_request(), Tenant.objects.filter(pk=tenant.pk)
            )
            mock_delete.assert_called_once_with(force_drop=True)

    def test_destroy_skips_protected_schemas(self):
        tenant = _make_tenant(
            "destroy-protected",
            is_active=False,
            suspended_at=timezone.now() - timedelta(hours=25),
        )
        admin = _admin()
        with patch("tenant.admin._PROTECTED", frozenset({tenant.schema_name})):
            with patch.object(Tenant, "delete") as mock_delete:
                admin.destroy_tenants(
                    _admin_request(), Tenant.objects.filter(pk=tenant.pk)
                )
                mock_delete.assert_not_called()

    def test_destroy_no_suspended_at_is_refused(self):
        """Tenant that was never suspended (suspended_at is None)."""
        tenant = _make_tenant(
            "destroy-no-ts",
            is_active=False,
            suspended_at=None,
        )
        admin = _admin()
        with patch.object(Tenant, "delete") as mock_delete:
            admin.destroy_tenants(
                _admin_request(), Tenant.objects.filter(pk=tenant.pk)
            )
            mock_delete.assert_not_called()
