"""The store dashboard's widgets: labels, empty states, visibility."""

from __future__ import annotations

import json

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.template.loader import render_to_string
from django.test import RequestFactory
from django.utils import translation
from extra_settings.models import Setting

from admin.dashboard.store import queries, widgets
from order.enum.status import OrderStatus
from order.factories.order import OrderFactory
from tenant.models import TenantMembershipRole
from user.factories.account import UserAccountFactory

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]


def _request(user=None, **query):
    request = RequestFactory().get("/admin/", query)
    request.user = user or AnonymousUser()
    return request


def _context(widget_class, user=None, **query) -> dict:
    return widget_class(request=_request(user, **query)).get_context_data()


def _superuser():
    from tests.utils.staff import stamp_platform_identity

    return stamp_platform_identity(UserAccountFactory(admin=True))


@pytest.fixture(autouse=True)
def _fresh_cache():
    cache.clear()


def test_cached_data_holds_no_language():
    """Computed under el, shown under en: the labels come out English.

    The old single payload cached translated labels, so every admin saw
    the language of whoever warmed the cache first.
    """
    with translation.override("el"):
        queries.overview.get("30d")
        queries.sales_series.get("30d")
    with translation.override("en"):
        cards = _context(widgets.StoreKpis)["cards"]

    assert [str(card["label"]) for card in cards] == [
        "Revenue",
        "Orders",
        "Average order",
        "New customers",
    ]


def test_no_hex_colour_reaches_a_chart():
    """Series colours are theme variables, so dark mode recolours them."""
    OrderFactory(num_order_items=0)

    chart = _context(widgets.StoreSalesChart)["chart"]
    assert "#" not in chart["data"]
    assert "var(--color-" in chart["data"]
    for card in _context(widgets.StoreKpis)["cards"]:
        if card["spark"]:
            assert "#" not in card["spark"]["data"]


class TestPeriodAndMeasure:
    def test_the_querystring_picks_the_period_and_the_measure(self):
        context = _context(
            widgets.StoreSalesChart, period="7d", metric="orders"
        )

        assert str(context["title"]) == "Orders"
        assert len(json.loads(context["chart"]["data"])["labels"]) == 7
        orders_link = next(m for m in context["metrics"] if m["active"])
        assert "period=7d" in orders_link["href"]

    def test_an_unknown_choice_falls_back_to_the_defaults(self):
        context = _context(
            widgets.StoreSalesChart, period="forever", metric="x"
        )

        assert str(context["title"]) == "Revenue"
        labels = json.loads(context["chart"]["data"])["labels"]
        assert len(labels) == 30

    def test_the_header_marks_the_period_on_display(self):
        context = _context(
            widgets.StoreDashboardHeader, _superuser(), period="12m"
        )

        active = [p for p in context["periods"] if p["active"]]
        assert [str(p["label"]) for p in active] == ["12 months"]
        assert "metric=revenue" in active[0]["href"]

    def test_the_new_product_button_needs_the_add_permission(self):
        assert (
            _context(widgets.StoreDashboardHeader)["new_product_href"] is None
        )
        assert _context(widgets.StoreDashboardHeader, _superuser())[
            "new_product_href"
        ]


class TestKpis:
    def test_a_trend_needs_a_period_before_to_compare(self):
        cards = _context(widgets.StoreKpis)["cards"]

        assert all(card["trend"] is None for card in cards)

    def test_an_average_of_no_paid_orders_is_a_dash_not_zero(self):
        cards = {
            str(card["label"]): card
            for card in _context(widgets.StoreKpis)["cards"]
        }

        assert cards["Average order"]["value"] == "—"
        assert cards["Average order"]["trend"] is None

    def test_links_only_where_the_viewer_may_go(self):
        anonymous = _context(widgets.StoreKpis)["cards"]
        assert all(card["href"] is None for card in anonymous)

        cards = _context(widgets.StoreKpis, _superuser())["cards"]
        assert cards[0]["href"].endswith("payment_status__exact=COMPLETED")


class TestAttention:
    def _labels(self, user, **settings_values) -> list[str]:
        for name, value in settings_values.items():
            Setting.objects.update_or_create(
                name=name, defaults={"value_type": "bool", "value_bool": value}
            )
        return [
            str(row["label"])
            for row in _context(widgets.StoreAttention, user)["rows"]
        ]

    def test_a_row_needs_work_waiting(self):
        assert self._labels(_superuser()) == []

        OrderFactory(num_order_items=0, status=OrderStatus.PENDING)
        cache.clear()
        assert self._labels(_superuser()) == ["Orders to process"]

    def test_a_switched_off_feature_has_no_row(self):
        from product.enum.review import ReviewStatus
        from product.factories.review import ProductReviewFactory

        ProductReviewFactory(status=ReviewStatus.NEW)

        assert "Reviews to approve" in self._labels(
            _superuser(), PRODUCT_REVIEWS_ENABLED=True
        )
        cache.clear()
        assert "Reviews to approve" not in self._labels(
            _superuser(), PRODUCT_REVIEWS_ENABLED=False
        )

    def test_a_row_needs_a_page_the_viewer_may_open(self):
        OrderFactory(num_order_items=0, status=OrderStatus.PENDING)

        assert self._labels(AnonymousUser()) == []

    def test_nothing_waiting_says_so(self):
        html = render_to_string(
            "admin/dashboard/store/attention.html",
            _context(widgets.StoreAttention, _superuser()),
        )
        assert "All caught up" in html

    def test_receipts_myDATA_rejected_reach_whoever_may_open_receipts(self):
        """The store's own tax documents: every viewer of receipts sees
        the rejections while the store sends them to myDATA, not only
        platform superusers."""
        from order.models.invoice import Invoice, MyDataStatus

        Invoice.objects.create(
            order=OrderFactory(num_order_items=0),
            invoice_number="INV-2026-000901",
            mydata_status=MyDataStatus.REJECTED,
        )

        assert "Receipts rejected by myDATA" in self._labels(
            _superuser(), MYDATA_ENABLED=True
        )
        cache.clear()
        assert "Receipts rejected by myDATA" not in self._labels(
            _superuser(), MYDATA_ENABLED=False
        )

    def test_a_rolling_count_opens_its_list_with_the_same_window(self):
        """The row counts the last N days; the list it opens must not
        show everything since the beginning."""
        from contact.factories import ContactFactory

        ContactFactory()
        rows = {
            str(row["label"]): row
            for row in _context(widgets.StoreAttention, _superuser())["rows"]
        }

        assert "created_at__gte=" in rows["New messages"]["href"]
        assert str(rows["New messages"]["detail"]) == "In the last 7 days"

    def test_myDATA_rejections_are_not_also_an_alert(self):
        """One place for them: the row above, not the superuser alert."""
        from order.models.invoice import Invoice, MyDataStatus

        Setting.objects.update_or_create(
            name="MYDATA_ENABLED",
            defaults={"value_type": "bool", "value_bool": True},
        )
        Invoice.objects.create(
            order=OrderFactory(num_order_items=0),
            invoice_number="INV-2026-000902",
            mydata_status=MyDataStatus.REJECTED,
        )
        titles = [
            str(alert["title"])
            for alert in _context(widgets.StoreAlerts, _superuser())["alerts"]
        ]

        assert not any("rejected" in title for title in titles)


def test_an_empty_source_card_renders_an_explanation():
    html = render_to_string(
        "admin/dashboard/store/sources.html",
        _context(widgets.StoreOrderSources),
    )
    assert "attributed" in html


def test_best_sellers_link_each_product():
    from order.enum.status import PaymentStatus
    from order.factories.item import OrderItemFactory
    from product.factories.product import ProductFactory

    product = ProductFactory()
    order = OrderFactory(
        num_order_items=0, payment_status=PaymentStatus.COMPLETED
    )
    OrderItemFactory(
        order=order,
        product=product,
        quantity=2,
        original_quantity=2,
        refunded_quantity=0,
        is_refunded=False,
    )

    rows = _context(widgets.StoreBestSellers)["table"]["rows"]

    assert f"/product/product/{product.id}/" in str(rows[0][0])
    assert rows[0][1]["content"] == "2"


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

    def _mydata_titles(self, tenant, bind_tenant, *, ground: str) -> list[str]:
        from extra_settings.models import Setting

        Setting.objects.update_or_create(
            name="MYDATA_ENABLED",
            defaults={"value_type": "bool", "value_bool": True},
        )
        Setting.objects.update_or_create(
            name="MYDATA_FHM_EXEMPTION_GROUND",
            defaults={"value_type": "string", "value_string": ground},
        )
        bind_tenant(tenant)
        superuser = UserAccountFactory(admin=True)
        with translation.override("en"):
            return [
                str(alert["title"])
                for alert in _context(widgets.StoreAlerts, superuser)["alerts"]
            ]

    def test_mydata_on_without_an_exemption_ground_is_a_danger_alert(
        self, bind_tenant
    ):
        """Switched on with credentials but no recorded ΦΗΜ exemption, the
        platform issues nothing; the owner is told why, not left guessing."""
        from tests.utils.staff import store_tenant

        titles = self._mydata_titles(
            store_tenant(
                "widget_mydata_no_ground",
                mydata_user_id="u",
                mydata_subscription_key="k",
            ),
            bind_tenant,
            ground="",
        )

        assert "myDATA is on, but no receipt is issued" in titles

    def test_a_recorded_exemption_ground_raises_no_alert(self, bind_tenant):
        from tests.utils.staff import store_tenant

        titles = self._mydata_titles(
            store_tenant(
                "widget_mydata_ground",
                mydata_user_id="u",
                mydata_subscription_key="k",
            ),
            bind_tenant,
            ground="ΠΟΛ.1002/2014 §4, accountant, 2026-10-14",
        )

        assert "myDATA is on, but no receipt is issued" not in titles

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
