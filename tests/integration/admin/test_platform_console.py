"""The platform console must not look or behave like a store's admin.

``platform.grooveshop.space`` serves the PUBLIC schema — the control
plane. Three defects reported from production on 2026-08-21, all caused
by platform-wide settings carrying tenant #1's identity:

- the sidebar read "Webside" and the login page "Welcome back to
  Webside Admin" — the control plane now has its own Unfold config,
  ``UNFOLD_PLATFORM``, as its single identity source;
- logging in at ``/admin/login`` landed on ``https://webside.gr/account``
  — Django's ``LoginView`` falls back to ``LOGIN_REDIRECT_URL`` and
  Unfold's login template never renders the ``next`` hidden field;
- every per-store sidebar section rendered on the control plane, 30
  links that all lead to a 403 — the control plane is now its own
  ``PlatformAdminSite`` with its own sidebar, and the store admin is
  mounted on store hosts only.
"""

from __future__ import annotations

from django.contrib.auth import REDIRECT_FIELD_NAME
from django.test import RequestFactory, TestCase
from django_tenants.utils import get_public_schema_name


class _Schema:
    """Stand-in for what TenantMainMiddleware puts on ``request.tenant``."""

    def __init__(self, schema_name: str) -> None:
        self.schema_name = schema_name


def _request(path: str = "/admin/", schema: str | None = None):
    request = RequestFactory().get(path)
    if schema is not None:
        request.tenant = _Schema(schema)
    return request


class TestPlatformBranding(TestCase):
    """The control plane's identity has one source: ``UNFOLD_PLATFORM``."""

    def _context(self):
        from django.conf import settings

        from admin.platform_site import platform_admin_site
        from user.models import UserAccount

        request = _request(schema=get_public_schema_name())
        request.user = UserAccount(
            email="probe@example.com",
            is_staff=True,
            is_superuser=True,
            is_active=True,
        )
        return settings.UNFOLD_PLATFORM, platform_admin_site.each_context(
            request
        )

    def test_platform_console_wears_the_platform_identity(self):
        config, ctx = self._context()
        assert ctx["site_header"] == config["SITE_HEADER"]
        assert str(ctx["site_title"]) == str(config["SITE_TITLE"])
        assert "Webside" not in str(ctx["site_header"])

    def test_platform_console_shows_no_merchant_logo(self):
        _config, ctx = self._context()
        assert ctx["site_logo"] is None
        assert ctx["site_icon"] is None
        assert ctx["site_favicons"] == []


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


class TestPlatformOnlySectionsLiveOnTheControlPlane(TestCase):
    """Every app label in ``tenant.role_scopes.PLATFORM_ONLY_APP_LABELS``
    that a sidebar can link belongs to the control plane, not to a
    store's console. A store role never gets a permission on any of
    them, so from a merchant's admin the links 403; for a platform
    superuser they are worse than useless — reported 2026-09-12 from
    ``api.webside.gr/admin`` ("should I see five Sites here?").

    Two distinct failures hide behind the same symptom, and both are
    covered here:

    - **Public-schema data shown as the store's.** ``sites``,
      ``country``, ``region``, ``core`` and the Celery tables live in
      the public schema only, so a tenant host's search path falls
      through to them. ``core.CachePurgeLog`` showed one merchant every
      purge ever run across every store, actor column included.
    - **Privilege surface.** ``auth_group`` does exist per tenant, so it
      leaks nothing — but no Group is created anywhere in the codebase
      (access is derived from ``UserTenantMembership`` roles), and a
      store admin who reached the section could mint themselves any
      permission. Group management is a control-plane concern.
    """

    INFRA_LABELS = frozenset(
        {
            "sites",
            "country",
            "region",
            "django_celery_beat",
            "django_celery_results",
            "auth",
            "core",
        }
    )

    @staticmethod
    def _linked(navigation) -> set[tuple[str, str]]:
        """(app_label, model_name) of every changelist a sidebar links,
        nested groups included."""

        def walk(items):
            for item in items:
                parts = [p for p in str(item.get("link", "")).split("/") if p]
                if "admin" in parts:
                    i = parts.index("admin")
                    if len(parts) > i + 2:
                        yield parts[i + 1], parts[i + 2]
                yield from walk(item.get("items", []))

        return set(walk(navigation))

    def test_the_store_sidebar_links_none_of_them(self):
        from django.conf import settings

        linked = self._linked(settings.UNFOLD["SIDEBAR"]["navigation"])
        stray = {pair for pair in linked if pair[0] in self.INFRA_LABELS}
        assert not stray, (
            f"platform-only sections in the store sidebar: {stray}"
        )

    def test_the_platform_sidebar_links_every_model_of_them(self):
        from django.conf import settings

        from admin.platform_site import platform_admin_site

        registered = {
            (model._meta.app_label, model._meta.model_name)
            for model in platform_admin_site._registry
            if model._meta.app_label in self.INFRA_LABELS
        }
        linked = self._linked(settings.UNFOLD_PLATFORM["SIDEBAR"]["navigation"])
        assert registered, "the platform site registers none of these apps"
        assert registered <= linked, (
            "registered on the control plane but reachable only by URL: "
            f"{registered - linked}"
        )
