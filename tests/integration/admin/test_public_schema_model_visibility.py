"""Tenant-only models must not be offered by the PUBLIC (platform) admin.

Their tables live only inside tenant schemas. Opening one while serving
public is never valid, and it does not fail cleanly: the
pre-multi-tenant public schema still carries same-named legacy tables
that TENANT_APPS migrations never touch, so the changelist queried
``public.order_order`` and raised ``column
order_order.loyalty_discount_currency does not exist`` — a 500 on the
platform operator's own control plane. Observed live on staging for
order, page_config, shipping_acs and shipping_boxnow.

Pruning the legacy rows does NOT make this safe: the query would then
fail on a missing relation instead. The model has to be withheld.

The filter lives on the AdminSite rather than on our ModelAdmin base
for two reasons. The tenant-only set includes THIRD-PARTY apps we do
not own the admin classes for — djstripe alone registers ~48 models,
plus allauth account/socialaccount/mfa and knox — so a base-class guard
could never cover them. And denying the model permissions outright was
tried and reverted: the condition has to be "am I serving public", but
outside a request there is no tenant on the connection, so it also
fired during tests, management commands and Celery work and denied 35
valid admin changelists.
"""

from django.contrib import admin as django_admin
from django.test import RequestFactory, TestCase
from django_tenants.utils import get_public_schema_name, schema_context

from tenant.app_labels import tenant_only_app_labels


class TestTenantOnlyAppLabels(TestCase):
    def test_excludes_apps_that_also_live_in_shared(self):
        labels = set(tenant_only_app_labels())
        # In BOTH lists -> has a public copy -> must not be called
        # tenant-only, or the admin would hide platform-critical models.
        assert "user" not in labels
        assert "extra_settings" not in labels
        assert "tenant" not in labels

    def test_includes_the_apps_that_500d_on_the_platform_host(self):
        labels = set(tenant_only_app_labels())
        for app in ("order", "page_config", "shipping_acs", "shipping_boxnow"):
            assert app in labels, f"{app} must be treated as tenant-only"

    def test_includes_third_party_tenant_apps(self):
        # These are why the filter cannot live on our ModelAdmin base.
        labels = set(tenant_only_app_labels())
        assert "djstripe" in labels


class TestPublicSchemaAdminAppList(TestCase):
    """The real lever: ``MyAdminSite.get_app_list`` on the public schema."""

    def _app_labels_for(self, schema: str) -> set[str]:
        from user.models import UserAccount

        request = RequestFactory().get("/admin/")
        site = django_admin.site
        with schema_context(schema):
            # A superuser passes every model permission, so whatever the
            # list still omits was omitted by the schema filter alone.
            request.user = UserAccount(
                email="platform-probe@example.com",
                is_staff=True,
                is_superuser=True,
                is_active=True,
            )
            app_list = site.get_app_list(request)
        return {app["app_label"] for app in app_list}

    def test_public_schema_hides_every_tenant_only_app(self):
        shown = self._app_labels_for(get_public_schema_name())
        leaked = shown & set(tenant_only_app_labels())
        assert not leaked, (
            "tenant-only apps offered by the platform admin "
            f"(each one 500s when opened): {sorted(leaked)}"
        )

    def test_public_schema_still_shows_the_platform_apps(self):
        """The opposite failure: hiding everything would empty the
        control plane the operator actually needs."""
        shown = self._app_labels_for(get_public_schema_name())
        assert "tenant" in shown, "platform admin must still manage tenants"


class TestWithheldOnPublicIsRequestGated(TestCase):
    """The withholding must key on the REQUEST, not on ambient state.

    Keying on ``connection.tenant``/``get_current_tenant()`` and
    defaulting to "withhold when unknown" denied 35 valid admin
    changelists, because that is the state during tests, management
    commands and Celery work. Only positive knowledge that the request
    is on the public schema may withhold.
    """

    def _order_admin(self):
        from order.models.order import Order

        model_admin = django_admin.site._registry.get(Order)
        assert model_admin is not None, "Order admin must be registered"
        return model_admin

    def _request(self, tenant):
        request = RequestFactory().get("/admin/order/order/")
        if tenant is not None:
            request.tenant = tenant
        return request

    def test_public_request_withholds_a_tenant_only_model(self):
        public = type("_T", (), {"schema_name": get_public_schema_name()})()
        assert self._order_admin()._withheld_on_public(self._request(public))

    def test_tenant_request_does_not_withhold(self):
        tenant = type("_T", (), {"schema_name": "webside"})()
        assert not self._order_admin()._withheld_on_public(
            self._request(tenant)
        )

    def test_request_without_a_tenant_does_not_withhold(self):
        """Tests / commands / Celery — unknown is not public."""
        assert not self._order_admin()._withheld_on_public(self._request(None))

    def test_shared_model_is_never_withheld_even_on_public(self):
        """Hiding platform models would empty the control plane.

        ``tenant`` is in SHARED_APPS, so it is not in the tenant-only
        set at all. TenantAdmin does not even inherit the base carrying
        the guard — assert both facts, since either one alone would keep
        it visible.
        """
        from tenant.models import Tenant as TenantModel

        assert "tenant" not in set(tenant_only_app_labels())

        model_admin = django_admin.site._registry.get(TenantModel)
        assert model_admin is not None
        guard = getattr(model_admin, "_withheld_on_public", None)
        if guard is None:
            return  # no guard to apply -> never withheld
        public = type("_T", (), {"schema_name": get_public_schema_name()})()
        assert not guard(self._request(public))
