"""Tests for the Stage 2 admin dashboard.

Cover:
* Cache hit / miss + invalidation via ``DASHBOARD_CACHE_KEY``.
* Zone D (system warnings) is gated on ``request.user.is_superuser``.
* ``low_stock_products`` excludes ``stock=0`` and respects the cap.
* Hero revenue periods sum per window and surface a non-None trend %
  once the prior window of equal length has data.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from admin.dashboard import (
    DASHBOARD_CACHE_KEY,
    REVENUE_PERIODS,
    _build_cached_zones,
    _check_low_stock,
    dashboard_callback,
)

User = get_user_model()


# Force LocMem cache for these tests so pickle of patched MagicMock objects
# never hits a Redis backend (which surfaces as a "not the same object as
# unittest.mock.MagicMock" error in some environments — Redis stores the
# pickle bytes alongside the class qualified name, and parallel xdist
# workers can race on conftest-level CACHES overrides).
@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "dashboard-test-cache",
        }
    }
)
class DashboardCallbackCachingTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        cache.delete(DASHBOARD_CACHE_KEY)

    def _make_request(self, *, superuser: bool):
        request = self.factory.get("/admin/")
        request.user = User.objects.create_user(
            username="staff",
            email="staff@example.com",
            password="x",
            is_staff=True,
            is_superuser=superuser,
        )
        return request

    def test_callback_populates_zones_a_b_c_e(self):
        request = self._make_request(superuser=True)
        ctx: dict = {}

        result = dashboard_callback(request, ctx)

        for key in (
            "hero",
            "performance_chart_data",
            "performance_chart_options",
            "status_chart_data",
            "status_chart_options",
            "orders_queue",
            "reviews_queue",
            "messages_queue",
            "funnel",
            "retention",
            "top_products",
        ):
            self.assertIn(key, result, f"missing zone key {key!r}")

    def test_callback_caches_zones(self):
        # Patch ``admin.dashboard.cache`` directly rather than seeding
        # the real ``cache`` proxy. The proxy resolves through the
        # production Redis backend on CI (conftest's ``settings.CACHES``
        # patch can't reset the already-materialised ``CacheHandler``
        # registry — Channels middleware tests depend on Redis staying
        # bound there). Without this patch, ``cache.get_or_set`` was
        # racing against the post-save signal handler in
        # ``admin/signals.py`` that invalidates ``DASHBOARD_CACHE_KEY``
        # whenever a ``UserAccount`` is created (``_make_request``
        # creates one) and, on cache-miss, attempted to pickle the
        # ``MagicMock`` builder's return value back into Redis —
        # ``PicklingError`` flaked the test in parallel runs.
        request = self._make_request(superuser=True)
        with (
            patch("admin.dashboard.cache") as cache_mock,
            patch("admin.dashboard._build_cached_zones") as builder,
        ):
            cache_mock.get_or_set.return_value = {"hero": {"_test": True}}
            ctx: dict = {}
            dashboard_callback(request, ctx)
            cache_mock.get_or_set.assert_called_once_with(
                DASHBOARD_CACHE_KEY, builder, 300
            )
            builder.assert_not_called()

        self.assertEqual(ctx.get("hero"), {"_test": True})

    def test_zone_d_hidden_for_non_superuser(self):
        request = self._make_request(superuser=False)
        ctx: dict = {}
        result = dashboard_callback(request, ctx)
        self.assertEqual(result["seller_config_warnings"], [])
        self.assertEqual(result["low_stock_products"], [])
        self.assertEqual(result["failed_celery_count"], 0)
        self.assertFalse(result["is_superuser"])

    def test_zone_d_visible_for_superuser(self):
        request = self._make_request(superuser=True)
        ctx: dict = {}
        result = dashboard_callback(request, ctx)
        # superuser path runs the fresh queries — keys are present
        self.assertTrue(result["is_superuser"])
        self.assertIn("seller_config_warnings", result)
        self.assertIn("mydata_warnings", result)
        self.assertIn("low_stock_products", result)
        self.assertIn("failed_celery_count", result)


class RevenuePeriodsTests(TestCase):
    """Hero revenue card: per-window sums + trend vs the prior window."""

    def setUp(self):
        # Revenue zones read Order rows — tenant-schema data. The test
        # DB runs as public, where the guard would serve _empty_zones()
        # and every assertion below would be vacuous.
        patcher = patch("admin.dashboard._is_public_schema", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _paid_order(self, amount: int, days_ago: int):
        from django.conf import settings as dj_settings
        from djmoney.money import Money

        from order.enum.status import PaymentStatus
        from order.factories.order import OrderFactory
        from order.models.order import Order

        order = OrderFactory(
            payment_status=PaymentStatus.COMPLETED,
            paid_amount=Money(amount, dj_settings.DEFAULT_CURRENCY),
            num_order_items=0,
        )
        # created_at is auto_now_add — backdate via queryset update.
        Order.objects.filter(pk=order.pk).update(
            created_at=timezone.now() - timedelta(days=days_ago)
        )
        return order

    def _periods(self) -> dict:
        hero = _build_cached_zones()["hero"]
        return {p["key"]: p for p in hero["revenue_periods"]}

    def test_all_configured_periods_present_in_order(self):
        hero = _build_cached_zones()["hero"]
        self.assertEqual(
            [p["key"] for p in hero["revenue_periods"]],
            [key for key, *_ in REVENUE_PERIODS],
        )

    def test_amounts_split_by_window_and_trend_vs_prior(self):
        self._paid_order(100, days_ago=2)  # current 7d window
        self._paid_order(50, days_ago=10)  # prior 7d window, current 30d

        periods = self._periods()
        self.assertEqual(periods["7d"]["amount"], 100.0)
        # (100 - 50) / 50 → +100% vs the prior 7-day window
        self.assertEqual(periods["7d"]["trend_pct"], 100.0)
        self.assertEqual(periods["30d"]["amount"], 150.0)
        # No orders in days 30-60 — nothing to compare against.
        self.assertIsNone(periods["30d"]["trend_pct"])
        self.assertEqual(periods["365d"]["amount"], 150.0)

    def test_unpaid_orders_excluded(self):
        from django.conf import settings as dj_settings
        from djmoney.money import Money

        from order.enum.status import PaymentStatus
        from order.factories.order import OrderFactory

        OrderFactory(
            payment_status=PaymentStatus.PENDING,
            paid_amount=Money(999, dj_settings.DEFAULT_CURRENCY),
            num_order_items=0,
        )
        periods = self._periods()
        self.assertEqual(periods["7d"]["amount"], 0.0)
        self.assertIsNone(periods["7d"]["trend_pct"])


class DashboardRenderTests(TestCase):
    """Full render of ``/admin/`` — catches template/context drift the
    payload-level tests can't (e.g. the Alpine period switcher markup).
    """

    def test_index_renders_revenue_period_switcher(self):
        from django.test import Client
        from django.urls import reverse

        cache.delete(DASHBOARD_CACHE_KEY)
        admin_user = User.objects.create_superuser(
            username="boss", email="boss@example.com", password="x"
        )
        client = Client()
        # has_permission() now requires a PlatformStaffBackend session
        # (see admin.forms.PlatformAdminAuthenticationForm) — force_login()
        # otherwise defaults to the first AUTHENTICATION_BACKENDS entry.
        client.force_login(
            admin_user, backend="tenant.auth_backends.PlatformStaffBackend"
        )

        response = client.get(reverse("admin:index"))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        for key, *_ in REVENUE_PERIODS:
            self.assertIn(f'data-period="{key}"', content)
        # Alpine persistence key for the selected period
        self.assertIn("dashboard-revenue-period", content)


class LowStockBoundaryTests(TestCase):
    """``_check_low_stock`` must include 0<stock<10 and exclude stock=0."""

    def setUp(self):
        # These tests exercise TENANT-schema behaviour (Product data
        # exists); the test DB runs as public, where the guard would
        # short-circuit to [].
        patcher = patch("admin.dashboard._is_public_schema", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_excludes_zero_stock(self):
        from product.factories.product import ProductFactory

        ProductFactory(stock=0, active=True)
        ProductFactory(stock=5, active=True)
        ProductFactory(stock=15, active=True)

        rows = _check_low_stock()
        stocks = [r["stock"] for r in rows]
        self.assertIn(5, stocks)
        self.assertNotIn(0, stocks)
        self.assertNotIn(15, stocks)

    def test_caps_at_ten_rows(self):
        from product.factories.product import ProductFactory

        # 12 products in the warning band — function caps the result at 10
        for i in range(12):
            ProductFactory(stock=1 + (i % 9), active=True)
        rows = _check_low_stock()
        self.assertLessEqual(len(rows), 10)


class SearchInsightsZoneTests(TestCase):
    """Zone F filtering rules, validated against real production data
    shapes: 93% empty-query placeholder rows, per-keystroke fragments
    ("P", "Po", "Pow"), and genuine greeklish queries.
    """

    def _make_query(self, query, results_count=5, days_ago=0):
        from search.models import SearchQuery

        row = SearchQuery.objects.create(
            query=query,
            language_code="el",
            content_type="federated",
            results_count=results_count,
            estimated_total_hits=results_count,
        )
        if days_ago:
            SearchQuery.objects.filter(pk=row.pk).update(
                timestamp=timezone.now() - timedelta(days=days_ago)
            )
        return row

    def _zone(self):
        from admin.dashboard import _zone_f_search_insights

        now = timezone.now()
        return _zone_f_search_insights(now, now - timedelta(days=30))[
            "search_insights"
        ]

    def test_empty_and_fragment_queries_are_excluded(self):
        self._make_query("")  # placeholder/browse request
        self._make_query("Po", results_count=0)  # keystroke fragment
        self._make_query("bataria kinitou")
        self._make_query("optiki ina", results_count=0)

        insights = self._zone()

        assert insights["searches_30d"] == 3  # empty excluded
        top = [row["query"] for row in insights["top_queries"]]
        assert "Po" not in top
        assert "bataria kinitou" in top
        zero = [row["query"] for row in insights["zero_queries"]]
        assert zero == ["optiki ina"]

    def test_lane_split_query_is_not_a_zero_result(self):
        # One user search logs separate product/blog/federated rows;
        # "windows" found 11 blog posts but 0 products — NOT a content
        # gap. Only never-successful queries are zero-result.
        self._make_query("windows", results_count=0)
        self._make_query("windows", results_count=11)
        self._make_query("asirmati skoupa", results_count=0)
        self._make_query("asirmati skoupa", results_count=0)

        insights = self._zone()

        zero = [row["query"] for row in insights["zero_queries"]]
        assert zero == ["asirmati skoupa"]
        assert insights["zero_result_count"] == 2  # dead query's rows only
        assert insights["zero_result_pct"] == 50.0  # 2 of 4 meaningful rows

    def test_old_rows_are_outside_the_window(self):
        self._make_query("klip kalodion", days_ago=45)
        insights = self._zone()
        assert insights["searches_30d"] == 0

    def test_click_metrics_render_dash_until_first_click(self):
        self._make_query("power bank")
        insights = self._zone()
        assert insights["clicks_30d"] is None
        assert insights["ctr_pct"] is None

    def test_click_metrics_appear_once_clicks_exist(self):
        from search.models import SearchClick

        row = self._make_query("power bank")
        SearchClick.objects.create(
            search_query=row,
            result_id="2",
            result_type="product",
            position=0,
        )
        insights = self._zone()
        assert insights["clicks_30d"] == 1
        assert insights["ctr_pct"] == 100.0
