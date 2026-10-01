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
    def test_configuration_alerts_are_for_superusers(self):
        staff = UserAccountFactory(is_staff=True)
        titles = [
            str(alert["title"])
            for alert in _context(widgets.StoreAlerts, staff)["alerts"]
        ]
        assert all("seller" not in title for title in titles)

    def test_missing_seller_details_are_reported(self, monkeypatch):
        from extra_settings.models import Setting

        monkeypatch.setattr(
            Setting, "get", classmethod(lambda cls, name, default=None: "")
        )
        superuser = UserAccountFactory(admin=True)
        with translation.override("en"):
            titles = [
                str(alert["title"])
                for alert in _context(widgets.StoreAlerts, superuser)["alerts"]
            ]
        assert "Invoice seller details are incomplete" in titles

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
