"""The store dashboard's widgets: labels, empty states, visibility."""

from __future__ import annotations

import json

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.template.loader import render_to_string
from django.test import RequestFactory
from django.utils import translation

from admin.dashboard.store import queries, widgets
from order.enum.status import OrderStatus
from order.factories.order import OrderFactory
from product.factories.product import ProductFactory
from tenant.models import TenantMembershipRole
from user.factories.account import UserAccountFactory

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]


def _request(user=None):
    request = RequestFactory().get("/admin/")
    request.user = user or AnonymousUser()
    return request


def _context(widget_class, user=None) -> dict:
    return widget_class(request=_request(user)).get_context_data()


@pytest.fixture(autouse=True)
def _fresh_cache():
    cache.clear()


def test_cached_data_holds_no_language():
    """Computed under el, shown under en: the labels come out English.

    The old single payload cached translated labels, so every admin saw
    the language of whoever warmed the cache first.
    """
    OrderFactory(num_order_items=0, status=OrderStatus.PENDING)

    with translation.override("el"):
        queries.order_status.get()
    with translation.override("en"):
        chart = _context(widgets.StoreOrderStatusChart)["chart"]

    assert json.loads(chart["data"])["labels"] == ["Pending"]


def test_no_hex_colour_reaches_a_chart():
    """Series colours are theme variables, so dark mode recolours them
    (the doughnut used to draw a #ffffff ring in dark mode)."""
    OrderFactory(num_order_items=0, status=OrderStatus.PENDING)

    for widget_class in (
        widgets.StoreOrderStatusChart,
        widgets.StorePerformanceChart,
    ):
        context = _context(widget_class)
        chart = context.get("chart") or context
        assert "#" not in chart["data"], widget_class.__name__
        assert "var(--color-" in chart["data"], widget_class.__name__


def test_an_empty_source_chart_renders_an_explanation_not_a_canvas():
    html = render_to_string(
        "admin/dashboard/components/doughnut_card.html",
        _context(widgets.StoreOrderSourceChart),
    )
    assert "<canvas" not in html
    assert "attributed" in html


class TestLowStock:
    def test_superusers_only(self):
        ProductFactory(stock=3, active=True)

        assert _context(widgets.StoreLowStock)["visible"] is False
        superuser = UserAccountFactory(admin=True)
        assert _context(widgets.StoreLowStock, superuser)["visible"] is True

    def test_warning_band_and_cap(self):
        superuser = UserAccountFactory(admin=True)
        ProductFactory(stock=0, active=True)
        ProductFactory(stock=15, active=True)
        for stock in range(1, 10):
            ProductFactory(stock=stock, active=True)
            ProductFactory(stock=stock, active=True)

        rows = _context(widgets.StoreLowStock, superuser)["table"]["rows"]

        assert len(rows) == 10


class TestAlerts:
    STOREFRONT = "Your business's legal details are missing"
    TAX_DOCUMENTS = "Details needed to issue receipts are missing"

    @staticmethod
    def _set(name: str, value) -> None:
        from extra_settings.models import Setting

        kind = "bool" if isinstance(value, bool) else "string"
        Setting.objects.update_or_create(
            name=name,
            defaults={"value_type": kind, f"value_{kind}": value},
        )

    def _fill(self, *, skip: tuple[str, ...] = ()) -> None:
        from tenant.legal_identity import (
            REQUIRED_DISCLOSURE_FIELDS,
            REQUIRED_TAX_DOCUMENT_FIELDS,
            SELLER_SETTING_KEYS,
        )

        for field in {
            *REQUIRED_DISCLOSURE_FIELDS,
            *REQUIRED_TAX_DOCUMENT_FIELDS,
        }:
            if field not in skip:
                self._set(SELLER_SETTING_KEYS[field], "x")

    def _alerts(self, user) -> dict[str, dict]:
        with translation.override("en"):
            return {
                str(alert["title"]): alert
                for alert in _context(widgets.StoreAlerts, user)["alerts"]
            }

    def test_a_store_that_issues_no_tax_documents_gets_only_the_notice(self):
        """webside: myDATA off and only the company name filled in."""
        self._set("MYDATA_ENABLED", False)
        self._set("INVOICE_SELLER_NAME", "Webside")

        alerts = self._alerts(UserAccountFactory(admin=True))

        assert self.TAX_DOCUMENTS not in alerts
        notice = alerts[self.STOREFRONT]
        assert notice["variant"] == "warning"
        assert [str(item) for item in notice["items"]] == [
            "Legal form (e.g. sole trader, IKE, AE)",
            "GEMI number",
            "VAT ID (ΑΦΜ)",
            "Address (street and number)",
            "City",
            "Postal code",
            "Country",
            "Contact email",
        ]

    def test_the_tax_office_is_not_a_storefront_requirement(self):
        self._set("MYDATA_ENABLED", False)
        self._fill(skip=("tax_office",))

        assert self._alerts(UserAccountFactory(admin=True)) == {}

    def test_missing_vat_id_is_danger_once_mydata_is_on(self):
        self._set("MYDATA_ENABLED", True)
        self._fill(skip=("vat_id",))

        alerts = self._alerts(UserAccountFactory(admin=True))

        alert = alerts[self.TAX_DOCUMENTS]
        assert alert["variant"] == "danger"
        assert [str(item) for item in alert["items"]] == ["VAT ID (ΑΦΜ)"]
        assert self.STOREFRONT in alerts

    def test_missing_tax_office_alone_needs_mydata(self):
        self._fill(skip=("tax_office",))
        self._set("MYDATA_ENABLED", True)

        alert = self._alerts(UserAccountFactory(admin=True))[self.TAX_DOCUMENTS]

        assert [str(item) for item in alert["items"]] == ["Tax office (ΔΟΥ)"]

    def test_a_complete_identity_raises_nothing(self):
        self._set("MYDATA_ENABLED", True)
        self._fill()

        alerts = self._alerts(UserAccountFactory(admin=True))

        assert self.STOREFRONT not in alerts
        assert self.TAX_DOCUMENTS not in alerts

    @pytest.mark.parametrize(
        ("role", "sees"),
        [
            (TenantMembershipRole.OWNER, True),
            (TenantMembershipRole.ADMIN, True),
            (TenantMembershipRole.STAFF, False),
        ],
    )
    def test_whoever_edits_store_settings_sees_the_alerts(
        self, bind_tenant, role, sees
    ):
        """The merchant fixes these, so the merchant is told - but staff,
        who cannot change settings, are not shown a task they cannot do."""
        from tests.utils.staff import store_staff, store_tenant

        tenant = store_tenant(f"widget_identity_{role}")
        bind_tenant(tenant)
        self._set("MYDATA_ENABLED", True)

        alerts = self._alerts(store_staff(tenant, role))

        assert (self.STOREFRONT in alerts) is sees
        assert (self.TAX_DOCUMENTS in alerts) is sees

    def test_a_merchant_never_sees_the_myDATA_credentials_alert(
        self, bind_tenant
    ):
        from tests.utils.staff import store_staff, store_tenant

        tenant = store_tenant("widget_identity_creds")
        bind_tenant(tenant)
        self._set("MYDATA_ENABLED", True)

        titles = self._alerts(store_staff(tenant, TenantMembershipRole.OWNER))

        assert not any("myDATA is enabled" in title for title in titles)

    def test_the_alerts_render_in_unfold_message_colours(self):
        self._set("MYDATA_ENABLED", True)
        self._set("INVOICE_SELLER_NAME", "Webside")

        with translation.override("en"):
            html = render_to_string(
                "admin/dashboard/components/alerts.html",
                _context(widgets.StoreAlerts, UserAccountFactory(admin=True)),
            )

        assert "bg-orange-100" in html
        assert "bg-red-100" in html
        assert "Fill in details" in html

    def _mydata_alert_items(self, tenant, bind_tenant) -> list[str] | None:
        from extra_settings.models import Setting

        Setting.objects.update_or_create(
            name="MYDATA_ENABLED",
            defaults={"value_type": "bool", "value_bool": True},
        )
        bind_tenant(tenant)
        superuser = UserAccountFactory(admin=True)
        with translation.override("en"):
            for alert in _context(widgets.StoreAlerts, superuser)["alerts"]:
                if "myDATA is enabled" in str(alert["title"]):
                    return list(alert["items"])
        return None

    def test_missing_mydata_credentials_are_read_off_the_tenant(
        self, bind_tenant
    ):
        from tests.utils.staff import store_tenant

        items = self._mydata_alert_items(
            store_tenant("widget_mydata_missing"), bind_tenant
        )

        assert items == ["mydata_user_id", "mydata_subscription_key"]

    def test_configured_mydata_credentials_raise_no_alert(self, bind_tenant):
        from tests.utils.staff import store_tenant

        items = self._mydata_alert_items(
            store_tenant(
                "widget_mydata_ok",
                mydata_user_id="u",
                mydata_subscription_key="k",
            ),
            bind_tenant,
        )

        assert items is None

    def test_a_store_dashboard_never_counts_background_task_failures(
        self, monkeypatch
    ):
        """Task results are public-schema only: one table holds every
        store's failures, so they belong on the control plane."""
        from django_celery_results.models import TaskResult
        from extra_settings.models import Setting

        # Every setting unset, so the superuser sees the alerts there are.
        monkeypatch.setattr(
            Setting, "get", classmethod(lambda cls, name, default=None: "")
        )

        TaskResult.objects.create(task_id="failed-task", status="FAILURE")
        superuser = UserAccountFactory(admin=True)
        with translation.override("en"):
            titles = [
                str(alert["title"]).lower()
                for alert in _context(widgets.StoreAlerts, superuser)["alerts"]
            ]
        assert titles
        assert not any("task" in title for title in titles)
