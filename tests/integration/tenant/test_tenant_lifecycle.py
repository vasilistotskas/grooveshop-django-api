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
from uuid import uuid4

import pytest
from django.contrib.messages import storage as messages_storage
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone, translation

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


def _admin_request(post=None, user=None):
    """Minimal mock of an HttpRequest sufficient for admin action calls.

    ``user`` defaults to a fresh superuser: the H2 audit trail
    (``self.log_change``/``self.log_deletion``) writes a real
    ``LogEntry`` keyed on ``request.user.pk``, which a bare
    ``MagicMock`` cannot satisfy (the FK write fails on a non-integer
    pk). ``post`` defaults to an empty dict — the destroy confirmation
    guard reads ``request.POST.get("destroy_confirmed")``.
    """
    req = MagicMock()
    req._messages = messages_storage.default_storage(req)
    req.POST = post if post is not None else {}
    req.user = user or User.objects.create_superuser(
        email=f"admin-action-{uuid4().hex[:10]}@example.com",
        username=f"adminaction{uuid4().hex[:10]}",
        password="testpass123",
    )
    return req


def _admin():
    """TenantAdmin instance (model_admin arg in actions)."""
    return TenantAdmin(Tenant, None)


@pytest.fixture(autouse=True)
def _no_schema_ddl():
    """``_make_tenant`` disables ``auto_create_schema`` on its own
    instance only — but the admin actions operate on a freshly-fetched
    queryset whose instances carry the class default (True), so their
    ``save()`` walked into django-tenants' schema healing
    (``create_schema`` → full ``migrate_schemas`` replay, ~90s per
    test). Patching the class attribute keeps every code path DDL-free,
    which is this module's stated contract.
    """
    with patch.object(Tenant, "auto_create_schema", False):
        yield


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

    def test_suspend_skips_protected_tenants(self):
        tenant = _make_tenant("suspend-guard", is_protected=True)
        admin = _admin()

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

    def test_activate_skips_protected_tenants(self):
        tenant = _make_tenant(
            "activate-guard",
            is_active=False,
            is_protected=True,
            suspended_at=timezone.now() - timedelta(hours=2),
        )
        admin = _admin()

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
        # Don't save — just test the delete method guard. The message
        # goes through gettext and the project's default locale is
        # Greek, so pin English rather than bet on the translation
        # catalogue.
        with (
            translation.override("en"),
            pytest.raises(ValidationError, match="protected system tenant"),
        ):
            public_tenant.delete()

    def test_delete_flagged_tenant_raises_validation_error(self):
        flagged = _make_tenant("flagged-del-test", is_protected=True)
        with (
            translation.override("en"),
            pytest.raises(ValidationError, match="protected system tenant"),
        ):
            flagged.delete()

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


@pytest.mark.django_db
class TestDeletePermissionOnProtectedTenants:
    """The admin must WITHHOLD delete on protected tenants, not 500.

    ``Tenant.delete()`` raises ``ValidationError`` for protected
    schemas, but the admin delete view only catches ``ProtectedError``
    — so while the permission was granted, the red delete button on
    ``public``/``webside`` led to an uncaught exception behind the
    confirm page. ``has_delete_permission`` is what hides the button
    and turns the URL into a 403.
    """

    def _request(self):
        from django.test import RequestFactory

        request = RequestFactory().get("/admin/")
        request.user = User.objects.create_superuser(
            email="delete-guard-operator@example.com",
            username="deleteguardoperator",
            password="testpass123",
        )
        return request

    def test_protected_tenant_is_not_deletable(self):
        tenant = _make_tenant("protected-del-perm", is_protected=True)
        assert _admin().has_delete_permission(self._request(), tenant) is False

    def test_ordinary_tenant_stays_deletable(self):
        tenant = _make_tenant("ordinary-del-perm")
        assert _admin().has_delete_permission(self._request(), tenant) is True

    def test_changelist_level_permission_is_untouched(self):
        """``obj=None`` (changelist) must keep the module-level answer."""
        assert _admin().has_delete_permission(self._request(), None) is True


# ---------------------------------------------------------------------------
# Destroy admin action
# ---------------------------------------------------------------------------


_CONFIRMED = {"destroy_confirmed": "yes"}


@pytest.mark.django_db
class TestDestroyTenants:
    """Every call here already carries ``destroy_confirmed=yes`` — the
    confirmation-guard behaviour itself is covered separately by
    ``TestDestroyConfirmationGuard``. These tests are only about the
    gates that run AFTER confirmation (protected/cooldown/suspended)."""

    def test_destroy_non_suspended_is_refused(self):
        tenant = _make_tenant("destroy-not-suspended", is_active=True)
        admin = _admin()
        with patch.object(Tenant, "delete") as mock_delete:
            admin.destroy_tenants(
                _admin_request(post=_CONFIRMED),
                Tenant.objects.filter(pk=tenant.pk),
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
                _admin_request(post=_CONFIRMED),
                Tenant.objects.filter(pk=tenant.pk),
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
                _admin_request(post=_CONFIRMED),
                Tenant.objects.filter(pk=tenant.pk),
            )
            mock_delete.assert_called_once_with(force_drop=True)

    def test_destroy_judges_the_row_as_it_is_now(self):
        """The gate re-reads the row under lock: a store protected (or
        re-activated) after the caller loaded its instance is refused."""
        from tenant.lifecycle import destroy_tenant

        stale = _make_tenant(
            "destroy-stale",
            is_active=False,
            suspended_at=timezone.now() - timedelta(hours=25),
        )
        Tenant.objects.filter(pk=stale.pk).update(is_protected=True)
        with patch.object(Tenant, "delete") as mock_delete:
            with pytest.raises(ValueError, match="Refusing to destroy"):
                destroy_tenant(stale)
            mock_delete.assert_not_called()
        assert Tenant.objects.filter(pk=stale.pk).exists()

    def test_destroy_skips_protected_tenants(self):
        tenant = _make_tenant(
            "destroy-protected",
            is_active=False,
            is_protected=True,
            suspended_at=timezone.now() - timedelta(hours=25),
        )
        admin = _admin()
        with patch.object(Tenant, "delete") as mock_delete:
            admin.destroy_tenants(
                _admin_request(post=_CONFIRMED),
                Tenant.objects.filter(pk=tenant.pk),
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
                _admin_request(post=_CONFIRMED),
                Tenant.objects.filter(pk=tenant.pk),
            )
            mock_delete.assert_not_called()


# ---------------------------------------------------------------------------
# Destroy confirmation guard (quick win #4)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDestroyConfirmationGuard:
    """A first submission must render an "are you sure" page and touch
    nothing; only a second POST carrying ``destroy_confirmed=yes``
    (as the confirmation page's own form re-submits) may proceed to
    the real gates in ``TestDestroyTenants``."""

    def _real_admin(self):
        """A ``TenantAdmin`` bound to a REAL ``AdminSite``.

        ``_admin()`` above passes ``admin_site=None``, which is fine
        for the confirmed path (it never touches ``self.admin_site``)
        but ``_destroy_confirmation_page`` calls
        ``self.admin_site.each_context(request)`` to render the page.
        """
        from django.contrib import admin as django_admin

        return django_admin.site._registry[Tenant]

    def _request(self, post=None):
        from importlib import import_module

        from django.conf import settings
        from django.test import RequestFactory

        # A real session (not just ``_messages``): Django's default
        # ``MESSAGE_STORAGE`` is session-backed, and
        # ``SessionStorage.__init__`` requires ``hasattr(request,
        # "session")`` — true for a ``MagicMock`` (used by every other
        # helper in this file) but false for a bare ``RequestFactory``
        # request, which has no ``.session`` at all without
        # SessionMiddleware. Mirrors
        # ``test_platform_billing_page.py``'s identical need.
        session_store = import_module(settings.SESSION_ENGINE).SessionStore
        request = RequestFactory().post(
            "/admin/tenant/tenant/", data=post or {}
        )
        request.session = session_store()
        request.user = User.objects.create_superuser(
            email=f"destroy-confirm-{uuid4().hex[:10]}@example.com",
            username=f"destroyconfirm{uuid4().hex[:10]}",
            password="testpass123",
        )
        request._messages = messages_storage.default_storage(request)
        return request

    def test_unconfirmed_post_renders_a_page_and_does_not_delete(self):
        tenant = _make_tenant(
            "destroy-unconfirmed",
            is_active=False,
            suspended_at=timezone.now() - timedelta(hours=25),
        )
        admin = self._real_admin()
        with patch.object(Tenant, "delete") as mock_delete:
            response = admin.destroy_tenants(
                self._request(), Tenant.objects.filter(pk=tenant.pk)
            )
            mock_delete.assert_not_called()

        assert response.status_code == 200
        content = response.content.decode()
        assert tenant.name in content
        assert tenant.schema_name in content

    def test_confirmed_post_reaches_the_real_gates(self):
        tenant = _make_tenant(
            "destroy-confirmed-flow",
            is_active=False,
            suspended_at=timezone.now() - timedelta(hours=25),
        )
        admin = self._real_admin()
        with patch.object(Tenant, "delete") as mock_delete:
            response = admin.destroy_tenants(
                self._request(post=_CONFIRMED),
                Tenant.objects.filter(pk=tenant.pk),
            )
            mock_delete.assert_called_once_with(force_drop=True)
        # No confirmation page — the bulk-action view handles the
        # (falsy) return value itself and redirects.
        assert response is None


# ---------------------------------------------------------------------------
# Audit trail on lifecycle actions (H2)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestLifecycleAuditTrail:
    """Suspend/activate/destroy must leave BOTH a Django ``LogEntry``
    (Unfold's History tab) and a ``Tenant.history`` row (django-simple-
    history) — not just the current field values."""

    def _log_entries_for(self, tenant):
        from django.contrib.admin.models import LogEntry
        from django.contrib.contenttypes.models import ContentType

        return LogEntry.objects.filter(
            content_type=ContentType.objects.get_for_model(Tenant),
            object_id=str(tenant.pk),
        )

    def test_suspend_writes_a_log_entry(self):
        tenant = _make_tenant("audit-suspend")
        admin = _admin()
        request = _admin_request()
        admin.suspend_tenants(request, Tenant.objects.filter(pk=tenant.pk))

        entries = self._log_entries_for(tenant)
        assert entries.exists()
        assert entries.first().user_id == request.user.pk

    def test_suspend_writes_a_historical_record(self):
        tenant = _make_tenant("audit-suspend-history")
        admin = _admin()
        admin.suspend_tenants(
            _admin_request(), Tenant.objects.filter(pk=tenant.pk)
        )

        tenant.refresh_from_db()
        history = tenant.history.order_by("-history_date")
        assert history.exists()
        assert history.first().is_active is False

    def test_re_suspend_does_not_duplicate_the_log_entry(self):
        """``suspend_tenant()`` no-ops on an already-suspended tenant
        (see ``TestSuspendTenant.test_re_suspend_does_not_reset_
        suspended_at``) — the admin must not log a change that never
        happened."""
        early = timezone.now() - timedelta(hours=25)
        tenant = _make_tenant(
            "audit-re-suspend", is_active=False, suspended_at=early
        )
        admin = _admin()
        admin.suspend_tenants(
            _admin_request(), Tenant.objects.filter(pk=tenant.pk)
        )
        assert not self._log_entries_for(tenant).exists()

    def test_activate_writes_a_log_entry(self):
        tenant = _make_tenant(
            "audit-activate",
            is_active=False,
            suspended_at=timezone.now() - timedelta(hours=2),
        )
        admin = _admin()
        request = _admin_request()
        admin.activate_tenants(request, Tenant.objects.filter(pk=tenant.pk))

        entries = self._log_entries_for(tenant)
        assert entries.exists()
        assert entries.first().user_id == request.user.pk

    def test_activate_writes_a_historical_record(self):
        tenant = _make_tenant(
            "audit-activate-history",
            is_active=False,
            suspended_at=timezone.now() - timedelta(hours=2),
        )
        admin = _admin()
        admin.activate_tenants(
            _admin_request(), Tenant.objects.filter(pk=tenant.pk)
        )

        tenant.refresh_from_db()
        history = tenant.history.order_by("-history_date")
        assert history.exists()
        assert history.first().is_active is True

    def test_destroy_writes_a_log_entry_before_the_row_is_gone(self):
        tenant = _make_tenant(
            "audit-destroy",
            is_active=False,
            suspended_at=timezone.now() - timedelta(hours=25),
        )
        tenant_pk = tenant.pk
        admin = _admin()
        request = _admin_request(post=_CONFIRMED)

        with patch.object(Tenant, "delete") as mock_delete:
            admin.destroy_tenants(request, Tenant.objects.filter(pk=tenant_pk))
            mock_delete.assert_called_once_with(force_drop=True)

        from django.contrib.admin.models import LogEntry
        from django.contrib.contenttypes.models import ContentType

        entries = LogEntry.objects.filter(
            content_type=ContentType.objects.get_for_model(Tenant),
            object_id=str(tenant_pk),
        )
        assert entries.exists()
        assert entries.first().user_id == request.user.pk
        assert entries.first().object_repr == str(tenant)
