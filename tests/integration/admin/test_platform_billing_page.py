"""The control plane's Plan & Billing page.

The PAGE's contract: mounted only on ``PlatformAdminSite``
(structurally absent from merchant admins), gated exactly like the rest
of the control plane, and rendering the estate's billing state from
public-schema data alone. The classifier it renders lives in
``tenant/billing.py`` and is pinned by
``tests/unit/tenant/test_billing_dunning.py``.
"""

from __future__ import annotations

from datetime import date

from django.test import RequestFactory, TestCase, override_settings
from django.urls import NoReverseMatch, resolve, reverse

# The classifier itself lives in tenant/billing.py and is pinned by
# tests/unit/tenant/test_billing_dunning.py — this file covers only the
# PAGE's shaping and gating.
from admin.platform_billing import _billing_rows, _billing_table
from admin.platform_site import platform_admin_site

_TODAY = date(2026, 8, 22)


class TestBillingRows(TestCase):
    @classmethod
    def setUpTestData(cls):
        from tenant.models import Tenant

        cls.store = Tenant(
            schema_name="billing_rows_tenant",
            name="Billing Rows Tenant",
            slug="billing-rows-tenant",
            owner_email="owner-billing-rows@example.com",
            plan="pro",
            paid_until=date(2030, 1, 1),
        )
        cls.store.auto_create_schema = False
        cls.store.save()

    def test_excludes_the_public_row(self):
        """`public` is the control plane itself, not a billable store."""
        schemas = {row["schema"] for row in _billing_rows(_TODAY)}
        assert "public" not in schemas

    def test_reports_the_store(self):
        row = next(
            r
            for r in _billing_rows(_TODAY)
            if r["schema"] == "billing_rows_tenant"
        )
        assert row["plan"] == "pro"
        assert row["state"] == "paid"

    def test_table_is_shaped_for_the_unfold_component(self):
        table = _billing_table(_billing_rows(_TODAY))
        assert "headers" in table and "rows" in table
        for row in table["rows"]:
            assert len(row) == len(table["headers"])

    def test_a_missing_term_renders_blank_not_a_date(self):
        table = _billing_table(
            [
                {
                    "name": "No Term Store",
                    "schema": "no_term",
                    "domain": "",
                    "plan": "basic",
                    "plan_display": "Basic",
                    "paid_until": None,
                    "state": "unbilled",
                }
            ]
        )
        assert table["rows"][0][3] == "—"


class TestPlanBillingRouting(TestCase):
    def test_lives_on_the_platform_namespace(self):
        match = resolve("/admin/plan-billing/", urlconf="tenant.urls_public")
        assert match.namespace == "platform_admin"

    def test_is_structurally_absent_from_the_merchant_admin(self):
        """Absent, not 403 — the merchant site must not know the name."""
        try:
            reverse("admin:plan_billing", urlconf="core.urls")
        except NoReverseMatch:
            return
        raise AssertionError("plan_billing is reversible on the merchant admin")

    @override_settings(ROOT_URLCONF="tenant.urls_public")
    def test_the_sidebar_links_to_it(self):
        """str() forces the lazy reverse, so the public urlconf (where
        the ``platform_admin`` namespace lives) must be active."""
        from django.conf import settings

        links = [
            str(item.get("link", ""))
            for group in settings.UNFOLD_PLATFORM["SIDEBAR"]["navigation"]
            for item in group.get("items", [])
        ]
        assert any("plan-billing" in link for link in links)


class TestPlanBillingPage(TestCase):
    @classmethod
    def setUpTestData(cls):
        from tenant.models import (
            Tenant,
            TenantMembershipRole,
            UserTenantMembership,
        )
        from user.models import UserAccount

        cls.operator = UserAccount.objects.create_superuser(
            email="billing-operator@example.com",
            username="billingoperator",
            password="testpass123",
        )
        cls.store = Tenant(
            schema_name="billing_page_tenant",
            name="Billing Page Tenant",
            slug="billing-page-tenant",
            owner_email="owner-billing-page@example.com",
            store_name="Billing Page Store",
            plan="enterprise",
            paid_until=date(2026, 8, 1),
        )
        cls.store.auto_create_schema = False
        cls.store.save()

        # The strongest non-platform identity: staff + OWNER membership.
        cls.merchant = UserAccount.objects.create_user(
            email="billing-merchant@example.com",
            username="billingmerchant",
            password="testpass123",
        )
        cls.merchant.is_staff = True
        cls.merchant.save(update_fields=["is_staff"])
        UserTenantMembership.objects.create(
            user=cls.merchant,
            tenant=cls.store,
            role=TenantMembershipRole.OWNER,
            is_active=True,
        )

    def _request(self, user):
        from importlib import import_module

        from django.conf import settings
        from django.contrib.auth import BACKEND_SESSION_KEY

        from tenant.auth_backends import PLATFORM_STAFF_BACKEND_PATH

        session_store = import_module(settings.SESSION_ENGINE).SessionStore
        request = RequestFactory().get("/admin/plan-billing/")
        request.urlconf = "tenant.urls_public"
        request.user = user
        request.session = session_store()
        request.session[BACKEND_SESSION_KEY] = PLATFORM_STAFF_BACKEND_PATH
        return request

    def _view(self):
        return resolve(
            "/admin/plan-billing/", urlconf="tenant.urls_public"
        ).func

    @override_settings(ROOT_URLCONF="tenant.urls_public")
    def test_renders_the_estate_for_an_operator(self):
        """Assert on DATA, not labels — labels go through gettext."""
        response = self._view()(self._request(self.operator))
        response.render()
        html = response.content.decode()
        assert self.store.store_name in html

    @override_settings(ROOT_URLCONF="tenant.urls_public")
    def test_no_template_comment_is_emitted(self):
        response = self._view()(self._request(self.operator))
        response.render()
        html = response.content.decode()
        for prose in ("PlanBillingView", "endcomment"):
            assert prose not in html, f"template comment leaked: {prose!r}"

    @override_settings(ROOT_URLCONF="tenant.urls_public")
    def test_a_store_owner_is_refused(self):
        """The page renders every store's billing state — merchant
        access would be the same estate leak the dashboard gate closed."""
        response = self._view()(self._request(self.merchant))
        assert response.status_code == 302
        assert self.store.store_name not in response.content.decode(
            errors="ignore"
        )

    def test_the_platform_gate_is_what_refuses_them(self):
        assert (
            platform_admin_site.has_permission(self._request(self.merchant))
            is False
        )
