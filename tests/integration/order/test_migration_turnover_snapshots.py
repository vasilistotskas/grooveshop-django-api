"""``0095_backfill_turnover_snapshots`` on the historical models.

The backfill functions run live in ``test_vat_snapshot``; this drives the
real ``MigrationExecutor`` across the data migration, because the
migration hands them the models of its point in the graph, and the receipt
code they call must read those the same way (the same harness as
``test_migration_amount_due``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase

BEFORE = ("order", "0094_order_shipped_at_ordervatline")
AFTER = ("order", "0095_backfill_turnover_snapshots")
SHIPPED = datetime(2026, 3, 3, 9, 30, tzinfo=UTC)


def _settle_deferred_constraints():
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")


def _migrate_to(target):
    executor = MigrationExecutor(connection)
    executor.migrate([target])
    executor.loader.build_graph()
    return executor.loader.project_state([target]).apps


class TestTurnoverSnapshotsMigration(TestCase):
    def _order_before(self):
        apps = _migrate_to(BEFORE)
        Order = apps.get_model("order", "Order")
        Product = apps.get_model("product", "Product")
        order = Order.objects.create(
            status="COMPLETED",
            shipping_price=Decimal("12.40"),
            payment_method_fee=Decimal("3.10"),
        )
        product = Product.objects.create(slug="migration-turnover")
        for price, rate in ((Decimal("124.00"), 24), (Decimal("56.50"), 13)):
            apps.get_model("order", "OrderItem").objects.create(
                order=order, product=product, price=price, vat_rate=rate
            )
        OrderHistory = apps.get_model("order", "OrderHistory")
        history = OrderHistory.objects.create(
            order=order,
            change_type="STATUS",
            new_value={"status": "SHIPPED"},
        )
        OrderHistory.objects.filter(pk=history.pk).update(created_at=SHIPPED)
        _settle_deferred_constraints()
        return order.pk

    def test_an_existing_order_gets_its_lines_and_its_ship_moment(self):
        pk = self._order_before()

        apps = _migrate_to(AFTER)

        Order = apps.get_model("order", "Order")
        lines = apps.get_model("order", "OrderVatLine").objects.filter(
            order_id=pk
        )
        self.assertEqual(Order.objects.get(pk=pk).shipped_at, SHIPPED)
        self.assertEqual(lines.count(), 6)
        self.assertEqual(
            sum(line.net + line.vat for line in lines),
            Decimal("124.00")
            + Decimal("56.50")
            + Decimal("12.40")
            + Decimal("3.10"),
        )

    def test_reversing_removes_nothing(self):
        pk = self._order_before()
        _migrate_to(AFTER)

        apps = _migrate_to(BEFORE)

        self.assertTrue(apps.get_model("order", "Order").objects.filter(pk=pk))
