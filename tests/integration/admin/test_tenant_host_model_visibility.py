"""Platform-owned models must not be offered by a TENANT host's admin.

The mirror of ``test_public_schema_model_visibility``. Those tables live
only in ``public``, so a store's admin showing them shows the PLATFORM's
rows wearing that merchant's page: ``/admin/sites/site/`` on
``api.webside.gr`` listed all five stores' domains, and
``/admin/core/cachepurgelog/`` all 146 purges across the estate, actor
column included. Reported from production 2026-09-12.

Removing the curated sidebar links was not enough — that closed the
navigation, not the URL, and the app list still carried seven such app
labels. ``has_module_permission`` returning False does both at once:
Django's ``_build_app_dict`` drops the model from the app list, and the
typed URL becomes a clean 403.

The coverage test below is the load-bearing one. The mixin has to be
applied at four places (``BaseModelAdmin``, the celery-beat admins, the
purge log, and the third-party override factory) because those classes
do not share a base, so "did we miss one" cannot be answered by reading
the code. It is answered here, against the live registry.
"""

from __future__ import annotations

from django.contrib import admin as django_admin
from django.test import RequestFactory, TestCase
from django_tenants.utils import get_public_schema_name

from admin.mixins import STORE_SELF_SERVICE_APP_LABELS
from tenant.app_labels import shared_only_app_labels, tenant_only_app_labels


class _Schema:
    def __init__(self, schema_name: str) -> None:
        self.schema_name = schema_name


def _request(schema: str | None):
    request = RequestFactory().get("/admin/")
    if schema is not None:
        request.tenant = _Schema(schema)
    return request


def _withheld_models():
    """Every registered model the store admin should refuse on a tenant."""
    hidden = set(shared_only_app_labels()) - STORE_SELF_SERVICE_APP_LABELS
    return [
        (model, admin)
        for model, admin in django_admin.site._registry.items()
        if model._meta.app_label in hidden
    ]


class TestSharedOnlyAppLabels(TestCase):
    def test_excludes_apps_that_also_live_in_tenant_schemas(self):
        labels = set(shared_only_app_labels())
        # Dual-listed -> each store has its own copy -> the store admin
        # is showing that store's rows, not the platform's.
        assert "user" not in labels
        assert "extra_settings" not in labels
        assert "auth" not in labels

    def test_excludes_tenant_only_apps(self):
        """``allauth_idp_oidc`` is the one that makes a hand-written list
        wrong: ``role_scopes.PLATFORM_ONLY_APP_LABELS`` names it, but its
        tables are TENANT-only, so a store genuinely owns those OIDC
        clients and must keep reaching them."""
        labels = set(shared_only_app_labels())
        assert "allauth_idp_oidc" not in labels
        assert not labels & set(tenant_only_app_labels())

    def test_includes_the_apps_that_leaked(self):
        labels = set(shared_only_app_labels())
        for label in (
            "sites",
            "core",
            "country",
            "region",
            "django_celery_beat",
            "django_celery_results",
        ):
            assert label in labels, f"{label} would still be offered"


class TestEveryPlatformModelIsWithheld(TestCase):
    """Coverage, proven against the registry rather than assumed."""

    def test_the_registry_actually_has_some(self):
        assert _withheld_models(), (
            "no shared-only model is registered on the store admin — this "
            "test would pass vacuously"
        )

    def test_none_of_them_is_offered_on_a_tenant_host(self):
        request = _request("webside")
        missed = [
            model._meta.label
            for model, admin in _withheld_models()
            if admin.has_module_permission(request)
        ]
        assert not missed, (
            f"{missed} still appear on a store's admin — their tables live "
            "only in public, so the page shows the platform's rows. The "
            "mixin was not applied to these admin classes."
        )

    def test_the_typed_url_is_refused_too(self):
        request = _request("webside")
        missed = [
            model._meta.label
            for model, admin in _withheld_models()
            if admin.has_view_permission(request)
        ]
        assert not missed, (
            f"{missed} are hidden from the sidebar but still render when "
            "the URL is typed"
        )


class TestWithholdingIsPositiveKnowledgeOnly(TestCase):
    def _site_admin(self):
        from django.contrib.sites.models import Site

        return django_admin.site._registry[Site]

    def test_a_tenant_request_withholds(self):
        assert self._site_admin()._withheld_on_tenant_host(_request("webside"))

    def test_the_platform_console_does_not(self):
        assert not self._site_admin()._withheld_on_tenant_host(
            _request(get_public_schema_name())
        )

    def test_a_request_without_a_tenant_does_not(self):
        """Management commands, Celery and tests bind no tenant. Keying on
        the connection instead once denied 35 valid changelists."""
        assert not self._site_admin()._withheld_on_tenant_host(_request(None))

    def test_a_store_model_is_never_withheld(self):
        from order.models import Order

        order_admin = django_admin.site._registry[Order]
        assert not order_admin._withheld_on_tenant_host(_request("webside"))


class TestMerchantSelfServiceSurvives(TestCase):
    """``tenant`` is SHARED-only but stays: ``TenantAdmin`` scopes a store
    operator to their own row and allowlists what they may edit. Hiding
    it would remove the merchant's own settings page."""

    def test_tenant_is_shared_only_yet_exempt(self):
        assert "tenant" in set(shared_only_app_labels())
        assert "tenant" in STORE_SELF_SERVICE_APP_LABELS

    def test_the_store_settings_page_is_never_a_withheld_model(self):
        """Asserted as an outcome, not by calling the predicate:
        ``TenantAdmin`` extends unfold's ``ModelAdmin`` directly and so
        does not carry the mixin at all. The exemption has to hold
        whichever way the class is built."""
        from tenant.models import Tenant

        assert Tenant not in {model for model, _ in _withheld_models()}
