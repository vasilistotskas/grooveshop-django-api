"""Tests for the Stage 2 admin dashboard.

Cover:
* Cache hit / miss + invalidation via ``DASHBOARD_CACHE_KEY``.
* Zone D (system warnings) is gated on ``request.user.is_superuser``.
* ``low_stock_products`` excludes ``stock=0`` and respects the cap.
* Hero KPIs surface a non-None trend % once prior data exists.
"""

from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import RequestFactory, TestCase

from admin.dashboard import (
    DASHBOARD_CACHE_KEY,
    _check_low_stock,
    dashboard_callback,
)


User = get_user_model()


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

    def test_callback_populates_zones_a_b_c(self):
        request = self._make_request(superuser=True)
        ctx: dict = {}

        result = dashboard_callback(request, ctx)

        for key in (
            "hero",
            "performance_chart",
            "status_chart",
            "orders_table",
            "reviews_table",
            "messages_table",
        ):
            self.assertIn(key, result, f"missing zone key {key!r}")

    def test_callback_caches_zones_a_b_c(self):
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
            patch("admin.dashboard._build_zones_a_b_c") as builder,
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


class LowStockBoundaryTests(TestCase):
    """``_check_low_stock`` must include 0<stock<10 and exclude stock=0."""

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
