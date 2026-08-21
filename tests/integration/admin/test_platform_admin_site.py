"""The control-plane admin is a separate site, not the tenant admin.

``platform.grooveshop.space`` manages tenants, staff and platform
settings; a merchant's admin manages that store's catalogue and orders.
They were the same site with sections hidden, which meant the control
plane rendered 30 per-store links that all answered 403, and wore
tenant #1's name and logo (production, 2026-08-21).

``PlatformAdminSite`` sets ``settings_name = "UNFOLD_PLATFORM"``;
``unfold.settings.get_config()`` resolves that name against settings and
merges it over ``CONFIG_DEFAULTS``, so the site carries its own
branding, sidebar and dashboard. Verified against django-unfold 0.104.1.
"""

from __future__ import annotations

from django.test import RequestFactory, TestCase, override_settings
from django.urls import Resolver404, resolve

from admin.platform_site import (
    PLATFORM_APP_LABELS,
    PlatformAdminSite,
    platform_admin_site,
)


class TestPlatformSiteConfiguration(TestCase):
    def test_uses_its_own_unfold_config_block(self):
        """Not UNFOLD — that block is tenant #1's branding and sidebar."""
        assert platform_admin_site.settings_name == "UNFOLD_PLATFORM"
        assert PlatformAdminSite.settings_name != "UNFOLD"

    def test_that_config_block_exists(self):
        from django.conf import settings

        assert hasattr(settings, "UNFOLD_PLATFORM")

    def test_unfold_resolves_the_platform_block(self):
        """The mechanism the whole design rests on."""
        from unfold.settings import get_config

        config = get_config(platform_admin_site.settings_name)
        assert config["SITE_HEADER"] == "Grooveshop Platform"
        assert "Webside" not in str(config["SITE_HEADER"])
        assert "Webside" not in str(config["SITE_TITLE"])

    def test_defaults_are_merged_in(self):
        """Only differences are declared; the rest falls back."""
        from unfold.settings import get_config

        config = get_config(platform_admin_site.settings_name)
        assert "COLORS" in config
        assert "LOGIN" in config

    def test_has_its_own_url_namespace(self):
        assert platform_admin_site.name == "platform_admin"


class TestPlatformSiteRegistry(TestCase):
    def test_registers_only_control_plane_apps(self):
        labels = {
            model._meta.app_label for model in platform_admin_site._registry
        }
        leaked = labels - set(PLATFORM_APP_LABELS)
        assert not leaked, f"non-platform apps on the control plane: {leaked}"

    def test_registers_the_models_the_operator_needs(self):
        names = {model.__name__ for model in platform_admin_site._registry}
        for required in ("Tenant", "TenantDomain", "UserTenantMembership"):
            assert required in names, f"{required} missing from the platform"

    def test_no_per_store_model_is_registered(self):
        """Per-store models must be ABSENT, not merely 403."""
        from tenant.app_labels import tenant_only_app_labels

        tenant_only = set(tenant_only_app_labels())
        labels = {
            model._meta.app_label for model in platform_admin_site._registry
        }
        assert not (labels & tenant_only)

    def test_reuses_the_shared_model_admin_classes(self):
        """Copied from the default registry so the two cannot drift."""
        from django.contrib import admin as django_admin
        from django.apps import apps

        Tenant = apps.get_model("tenant", "Tenant")
        shared = type(django_admin.site._registry[Tenant])
        platform = type(platform_admin_site._registry[Tenant])
        assert platform is shared


class TestSchemaRouting(TestCase):
    def test_public_admin_is_the_platform_site(self):
        match = resolve("/admin/", urlconf="tenant.urls_public")
        assert match.namespace == "platform_admin"

    def test_public_admin_serves_tenant_management(self):
        match = resolve("/admin/tenant/tenant/", urlconf="tenant.urls_public")
        assert match.namespace == "platform_admin"

    def test_tenant_admin_is_untouched(self):
        match = resolve("/admin/", urlconf="core.urls")
        assert match.namespace == "admin"

    def test_tenant_admin_still_serves_store_models(self):
        match = resolve("/admin/order/order/", urlconf="core.urls")
        assert match.namespace == "admin"

    def test_store_models_are_not_platform_pages(self):
        """They may 404 or not match — they must never be a real page."""
        try:
            match = resolve(
                "/admin/product/product/", urlconf="tenant.urls_public"
            )
        except Resolver404:
            return
        assert match.namespace == "platform_admin"
        assert "unfold.admin" not in match.func.__module__, (
            "a per-store changelist rendered on the control plane"
        )


class TestPlatformDashboard(TestCase):
    def test_reports_the_tenant_estate(self):
        from admin.platform_dashboard import dashboard_callback

        context = dashboard_callback(None, {})
        for key in (
            "platform_tenant_count",
            "platform_active_count",
            "platform_suspended_count",
            "platform_tenants",
        ):
            assert key in context, f"dashboard missing {key}"

    def test_excludes_the_public_row_from_the_estate(self):
        """`public` is the control plane itself, not a store."""
        from admin.platform_dashboard import dashboard_callback

        context = dashboard_callback(None, {})
        schemas = {row["schema"] for row in context["platform_tenants"]}
        assert "public" not in schemas

    def test_survives_a_tenant_whose_schema_is_not_ready(self):
        """A half-provisioned tenant must not 500 the control plane."""
        from admin.platform_dashboard import dashboard_callback
        from tenant.models import Tenant

        t = Tenant(
            schema_name="pop_test_dashboard_missing",
            name="Half provisioned",
            slug="half-provisioned",
            owner_email="x@example.com",
        )
        t.auto_create_schema = False
        t.save()
        try:
            context = dashboard_callback(None, {})
            row = next(
                r
                for r in context["platform_tenants"]
                if r["schema"] == "pop_test_dashboard_missing"
            )
            assert row["orders"] is None
        finally:
            Tenant.objects.filter(pk=t.pk).delete()


class TestPlatformDashboardPage(TestCase):
    """The control plane must render ITS dashboard, not a merchant's.

    ``AdminSite.index()`` falls back to ``"admin/index.html"``, which
    this project overrides globally with the store dashboard (revenue,
    pending orders, product shortcuts). Sharing it was the reported
    complaint: the control plane looked exactly like a tenant admin.
    """

    @classmethod
    def setUpTestData(cls):
        from user.models import UserAccount

        cls.user = UserAccount.objects.create_superuser(
            email="platform-dashboard@example.com",
            username="platformdashboard",
            password="testpass123",
        )

    def _render(self) -> str:
        """Render the control-plane index for a real platform-staff session.

        The session marker is load-bearing, not ceremony: Unfold only
        builds ``sidebar_navigation`` when ``has_permission()`` passes
        (``unfold/sites.py``), and this site's ``has_permission``
        requires a ``PlatformStaffBackend`` session. Without it the
        page silently falls back to the auto-generated app list and the
        curated navigation is never exercised.
        """
        from importlib import import_module

        from django.conf import settings
        from django.contrib.auth import BACKEND_SESSION_KEY

        from tenant.auth_backends import PLATFORM_STAFF_BACKEND_PATH

        session_store = import_module(settings.SESSION_ENGINE).SessionStore

        request = RequestFactory().get("/admin/")
        request.urlconf = "tenant.urls_public"
        request.user = self.user
        request.session = session_store()
        request.session[BACKEND_SESSION_KEY] = PLATFORM_STAFF_BACKEND_PATH

        response = platform_admin_site.index(request)
        response.render()
        return response.content.decode()

    def test_uses_its_own_index_template(self):
        assert platform_admin_site.index_template == (
            "admin/platform_index.html"
        )

    @override_settings(ROOT_URLCONF="tenant.urls_public")
    def test_renders(self):
        assert "Control plane" in self._render()

    @override_settings(ROOT_URLCONF="tenant.urls_public")
    def test_does_not_render_the_store_dashboard(self):
        html = self._render()
        for merchant_only in (
            "Overview of revenue",
            "Pending Orders",
            "New Product",
        ):
            assert merchant_only not in html, (
                f"the store dashboard leaked onto the control plane: "
                f"{merchant_only!r}"
            )

    @override_settings(ROOT_URLCONF="tenant.urls_public")
    def test_shows_the_tenant_estate(self):
        html = self._render()
        assert "Schema" in html
        assert "Scheduled tasks" in html

    @override_settings(ROOT_URLCONF="tenant.urls_public")
    def test_renders_the_curated_sidebar(self):
        """Not the auto app list.

        These two labels exist only in ``UNFOLD_PLATFORM``'s navigation
        — no model's verbose name matches them — so they are absent
        unless the curated sidebar really rendered.
        """
        html = self._render()
        for label in ("Platform Staff", "Scheduled Tasks"):
            assert label in html, f"curated sidebar missing {label!r}"

    @override_settings(ROOT_URLCONF="tenant.urls_public")
    def test_no_template_comment_is_emitted(self):
        """``{# #}`` is SINGLE-line in Django — a multi-line one renders.

        Caught in review: the file's header comment was being printed
        into the page as literal prose.
        """
        html = self._render()
        for prose in ("AdminSite.index()", "endcomment", "merchant"):
            assert prose not in html, f"template comment leaked: {prose!r}"


class TestPlatformDashboardTable(TestCase):
    def test_is_shaped_for_the_unfold_table_component(self):
        from admin.platform_dashboard import dashboard_callback

        table = dashboard_callback(None, {})["platform_tenants_table"]
        assert "headers" in table and "rows" in table
        for row in table["rows"]:
            assert len(row) == len(table["headers"])

    def test_unreadable_order_counts_render_blank_not_zero(self):
        """ "0 orders" would read as a real figure for a broken store."""
        from admin.platform_dashboard import _tenants_table

        table = _tenants_table(
            [
                {
                    "name": "Half provisioned",
                    "schema": "nope",
                    "plan": "",
                    "is_active": True,
                    "suspended": False,
                    "domain": "",
                    "orders": None,
                    "revenue": None,
                }
            ]
        )
        assert table["rows"][0][-1] == "—"


class TestAdminLoginLandsInTheAdmin(TestCase):
    """A staff login must not end up on the storefront.

    Without ``next``, Django falls back to ``LOGIN_REDIRECT_URL`` (the
    shopper account page) — production sent an operator signing in at
    the control plane to ``https://webside.gr/account`` on 2026-08-21.

    The fix lives in ``AdminSiteLoginNextMixin`` precisely so it applies
    to EVERY site; the platform site was added later and would silently
    have shipped without it.
    """

    def _stub_site(self, name: str):
        """A site whose ``login`` is observable, to test both branches."""
        from admin.mixins import AdminSiteLoginNextMixin

        class _Base:
            def login(self, request, extra_context=None):
                return "super-called"

        class _Site(AdminSiteLoginNextMixin, _Base):
            pass

        site = _Site()
        site.name = name
        return site

    @override_settings(ROOT_URLCONF="tenant.urls_public")
    def test_get_without_next_is_redirected_to_the_admin_index(self):
        site = self._stub_site("platform_admin")
        request = RequestFactory().get("/admin/login/")

        response = site.login(request)

        assert response.status_code == 302
        assert response["Location"] == "/admin/login/?next=/admin/"

    @override_settings(ROOT_URLCONF="tenant.urls_public")
    def test_the_target_is_never_the_storefront(self):
        from django.conf import settings

        site = self._stub_site("platform_admin")
        response = site.login(RequestFactory().get("/admin/login/"))

        assert settings.LOGIN_REDIRECT_URL not in response["Location"]

    @override_settings(ROOT_URLCONF="tenant.urls_public")
    def test_an_explicit_next_is_left_alone(self):
        """Only the missing-``next`` case is rewritten."""
        site = self._stub_site("platform_admin")
        request = RequestFactory().get("/admin/login/", {"next": "/admin/foo/"})

        assert site.login(request) == "super-called"

    def test_both_real_sites_carry_the_fix(self):
        """The regression guard: neither site may drop the mixin."""
        from admin.admin import MyAdminSite
        from admin.mixins import AdminSiteLoginNextMixin

        for site_cls in (PlatformAdminSite, MyAdminSite):
            assert issubclass(site_cls, AdminSiteLoginNextMixin), (
                f"{site_cls.__name__} would send staff to the storefront"
            )
