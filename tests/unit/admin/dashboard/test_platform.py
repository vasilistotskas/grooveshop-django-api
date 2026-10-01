"""The control plane's per-store figures: the snapshot and its widgets.

``refresh_stats_snapshot`` reads a store's orders inside its own schema;
the dashboard and the Tenants list only read the public-schema
snapshot. (The main test lane has one physical schema, so binding a
store with ``bind_store_tenant`` is enough to run the task's body.)
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory
from django.utils import timezone
from djmoney.money import Money

from admin.dashboard.platform import PlatformEstateCards, PlatformStoresTable
from order.enum.status import PaymentStatus
from order.factories.order import OrderFactory
from tenant.models import TenantStatsSnapshot
from tenant.tasks import refresh_stats_snapshot
from tests.utils.staff import (
    bind_store_tenant,
    store_tenant,
    unbind_store_tenant,
)

pytestmark = pytest.mark.django_db


def _order(status, amount):
    return OrderFactory(
        num_order_items=0,
        payment_status=status,
        paid_amount=Money(amount, settings.DEFAULT_CURRENCY),
    )


def _refresh(tenant):
    previous = bind_store_tenant(tenant)
    try:
        return refresh_stats_snapshot.run()
    finally:
        unbind_store_tenant(previous)


def _context(widget_class):
    request = RequestFactory().get("/admin/")
    request.user = AnonymousUser()
    return widget_class(request=request).get_context_data()


class TestSnapshot:
    def test_revenue_is_completed_payments_only(self):
        tenant = store_tenant("snapshot_revenue_store")
        _order(PaymentStatus.COMPLETED, 30)
        _order(PaymentStatus.COMPLETED, 20)
        _order(PaymentStatus.PENDING, 999)

        _refresh(tenant)

        snapshot = TenantStatsSnapshot.objects.get(tenant=tenant)
        assert snapshot.orders_count == 3
        assert snapshot.revenue == Decimal("50.00")
        assert snapshot.last_order_at is not None

    def test_no_orders_is_zero_not_missing(self):
        tenant = store_tenant("snapshot_empty_store")
        _refresh(tenant)

        snapshot = TenantStatsSnapshot.objects.get(tenant=tenant)
        assert (snapshot.orders_count, snapshot.revenue) == (0, Decimal(0))
        assert snapshot.last_order_at is None

    def test_a_refresh_replaces_the_previous_figures(self):
        tenant = store_tenant("snapshot_replace_store")
        _refresh(tenant)
        _order(PaymentStatus.COMPLETED, 10)
        _refresh(tenant)

        assert TenantStatsSnapshot.objects.get(tenant=tenant).orders_count == 1

    def test_refuses_to_run_outside_a_store(self):
        previous = bind_store_tenant(None)
        try:
            with pytest.raises(RuntimeError):
                refresh_stats_snapshot.run()
        finally:
            unbind_store_tenant(previous)


class TestStoresTable:
    def test_lists_stores_not_the_platform(self):
        store_tenant("public", name="Platform")
        store = store_tenant("table_listed_store", store_name="Listed Store")

        rows = _context(PlatformStoresTable)["table"]["rows"]
        names = " ".join(str(row[0]) for row in rows)

        assert "Listed Store" in names
        assert "Platform" not in names
        assert f"/tenant/tenant/{store.pk}/change/" in names

    def test_rows_line_up_with_headers(self):
        store_tenant("table_shape_store")
        table = _context(PlatformStoresTable)["table"]
        for row in table["rows"]:
            assert len(row) == len(table["headers"])

    def test_an_unread_store_shows_a_dash_not_zero(self):
        store_tenant("table_unread_store", store_name="Unread Store")
        row = next(
            row
            for row in _context(PlatformStoresTable)["table"]["rows"]
            if "Unread Store" in str(row[0])
        )
        assert row[4]["content"] == "—"
        assert row[5]["content"] == "—"

    def test_shows_the_snapshot(self):
        store = store_tenant("table_read_store", store_name="Read Store")
        TenantStatsSnapshot.objects.create(
            tenant=store,
            orders_count=1234,
            revenue=Decimal("99.50"),
            refreshed_at=timezone.now(),
        )
        row = next(
            row
            for row in _context(PlatformStoresTable)["table"]["rows"]
            if "Read Store" in str(row[0])
        )
        assert "1" in row[4]["content"] and "234" in row[4]["content"]
        assert "99" in row[5]["content"]

    def test_the_cost_does_not_grow_with_the_estate(
        self, django_assert_num_queries
    ):
        store_tenant("table_cost_store_1")
        with django_assert_num_queries(2):
            _context(PlatformStoresTable)
        for index in range(2, 7):
            store_tenant(f"table_cost_store_{index}")
        with django_assert_num_queries(2):
            _context(PlatformStoresTable)


def test_four_estate_cards():
    assert len(_context(PlatformEstateCards)["kpis"]) == 4
