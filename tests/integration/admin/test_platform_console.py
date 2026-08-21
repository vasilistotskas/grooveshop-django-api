"""The platform console must not look or behave like a store's admin.

``platform.grooveshop.space`` serves the PUBLIC schema — the control
plane. Three defects reported from production on 2026-08-21, all caused
by platform-wide settings carrying tenant #1's identity:

- the sidebar read "Webside" and the login page "Welcome back to
  Webside Admin", because ``get_current_tenant()`` is None on public so
  ``each_context`` fell through to the ``UNFOLD_SITE_HEADER`` defaults;
- logging in at ``/admin/login`` landed on ``https://webside.gr/account``
  — Django's ``LoginView`` falls back to ``LOGIN_REDIRECT_URL`` and
  Unfold's login template never renders the ``next`` hidden field;
- every per-store sidebar section rendered on the control plane, 30
  links that all lead to a 403.
"""

from __future__ import annotations

from django.contrib import admin as django_admin
from django.contrib.auth import REDIRECT_FIELD_NAME
from django.test import RequestFactory, TestCase
from django_tenants.utils import get_public_schema_name

from admin.admin import PLATFORM_SITE_HEADER
from admin.permissions import is_platform_section, is_store_section
from tenant.console import is_platform_console, is_tenant_console


class _Schema:
    """Stand-in for what TenantMainMiddleware puts on ``request.tenant``."""

    def __init__(self, schema_name: str) -> None:
        self.schema_name = schema_name


def _request(path: str = "/admin/", schema: str | None = None):
    request = RequestFactory().get(path)
    if schema is not None:
        request.tenant = _Schema(schema)
    return request


class TestConsoleDetection(TestCase):
    def test_public_schema_is_the_platform_console(self):
        r = _request(schema=get_public_schema_name())
        assert is_platform_console(r) is True
        assert is_tenant_console(r) is False

    def test_tenant_schema_is_not(self):
        r = _request(schema="webside")
        assert is_platform_console(r) is False
        assert is_tenant_console(r) is True

    def test_unknown_schema_is_neither(self):
        """Management commands, Celery and tests have no bound tenant.

        Both answers are False so callers can tell "definitely a tenant"
        from "no idea" — treating unknown as platform would reshape the
        admin for every one of those callers.
        """
        r = _request()
        assert is_platform_console(r) is False
        assert is_tenant_console(r) is False


class TestSidebarSectionGating(TestCase):
    def test_store_sections_hidden_on_the_platform_console(self):
        assert (
            is_store_section(_request(schema=get_public_schema_name())) is False
        )

    def test_store_sections_shown_on_a_tenant(self):
        assert is_store_section(_request(schema="webside")) is True

    def test_store_sections_shown_when_schema_unknown(self):
        """Hiding on unknown would blank the sidebar in tests/commands."""
        assert is_store_section(_request()) is True

    def test_platform_sections_are_the_inverse(self):
        assert (
            is_platform_section(_request(schema=get_public_schema_name()))
            is True
        )
        assert is_platform_section(_request(schema="webside")) is False

    def test_every_store_only_group_is_gated(self):
        """No per-store group may reach the control plane ungated."""
        from django.conf import settings

        from tenant.app_labels import tenant_only_app_labels

        tenant_only = set(tenant_only_app_labels())
        ungated = []
        for group in settings.UNFOLD["SIDEBAR"]["navigation"]:
            labels = set()
            for item in group.get("items", []):
                parts = [p for p in str(item.get("link", "")).split("/") if p]
                if "admin" in parts:
                    i = parts.index("admin")
                    if len(parts) > i + 1:
                        labels.add(parts[i + 1])
            if labels and labels <= tenant_only and "permission" not in group:
                ungated.append(str(group.get("title")))
        assert not ungated, (
            "per-store sidebar groups with no permission callback — they "
            f"render on the platform console and 403 when clicked: {ungated}"
        )


class TestPlatformBranding(TestCase):
    def _context(self, schema: str | None):
        from user.models import UserAccount

        request = _request(schema=schema)
        request.user = UserAccount(
            email="probe@example.com",
            is_staff=True,
            is_superuser=True,
            is_active=True,
        )
        return django_admin.site.each_context(request)

    def test_platform_console_does_not_wear_a_merchant_name(self):
        ctx = self._context(get_public_schema_name())
        assert ctx["site_header"] == PLATFORM_SITE_HEADER
        assert "Webside" not in str(ctx["site_header"])
        assert "Webside" not in str(ctx["site_title"])

    def test_platform_console_shows_no_merchant_logo(self):
        ctx = self._context(get_public_schema_name())
        assert ctx.get("site_logo") is None
        assert ctx.get("site_icon") is None

    def test_unknown_schema_keeps_the_defaults(self):
        ctx = self._context(None)
        assert ctx["site_header"] != PLATFORM_SITE_HEADER


class TestAdminLoginRedirect(TestCase):
    """A staff login must land in the admin, never on the storefront."""

    def test_bare_login_url_gets_a_next_pointing_at_the_admin(self):
        response = self.client.get("/admin/login/")
        assert response.status_code == 302
        assert f"{REDIRECT_FIELD_NAME}=" in response["Location"]
        assert "/admin/" in response["Location"]

    def test_next_is_not_the_storefront(self):
        from django.conf import settings

        response = self.client.get("/admin/login/")
        assert settings.LOGIN_REDIRECT_URL not in response["Location"], (
            "admin login still points at the storefront account page"
        )

    def test_an_explicit_next_is_preserved(self):
        target = "/admin/tenant/tenant/"
        response = self.client.get(
            f"/admin/login/?{REDIRECT_FIELD_NAME}={target}"
        )
        assert response.status_code == 200, (
            "a login URL that already carries next must render, not redirect"
        )
