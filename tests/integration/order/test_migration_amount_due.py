"""``0070_order_amount_due`` copies ``paid_amount`` into ``amount_due``.

Drives the real ``MigrationExecutor`` across the migration, in both
directions, with rows written through the historical models (the same
harness idea as ``tests/integration/pay_way/test_migration_key.py``).
PostgreSQL DDL is transactional, so the ``TestCase`` transaction undoes
the schema changes and leaves the worker's database at the latest
migration again.

``paid_amount`` held what the customer owed from the day it was written,
so the copy is a plain rename of the data. The old columns must survive in
the database: the release still serving selects and inserts them.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase

BEFORE = ("order", "0069_order_payment_id_unique")
AFTER = ("order", "0070_order_amount_due")


def _settle_deferred_constraints():
    """Rows written just before an ALTER leave pending FK trigger events,
    which PostgreSQL refuses to ALTER around inside one transaction."""
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")


def _migrate_to(target):
    executor = MigrationExecutor(connection)
    executor.migrate([target])
    executor.loader.build_graph()
    return executor.loader.project_state([target]).apps


def _migrate_forward():
    """Run the migration here: the test database is built at the latest
    migration, so ``_migrate_to(AFTER)`` alone would apply nothing and
    this module would be reading the build's result, not the SQL under
    test."""
    _migrate_to(BEFORE)
    return _migrate_to(AFTER)


def _columns() -> dict[str, tuple[str, str | None]]:
    """``order_order``'s columns: name -> (is_nullable, column_default)."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT column_name, is_nullable, column_default "
            "FROM information_schema.columns "
            "WHERE table_schema = current_schema() "
            "AND table_name = 'order_order'"
        )
        return {name: (nullable, default) for name, nullable, default in cursor}


class TestAmountDueMigration(TestCase):
    def _before_order(self, owed, currency="EUR", **fields):
        Order = _migrate_to(BEFORE).get_model("order", "Order")
        order = Order.objects.create(
            paid_amount=owed, paid_amount_currency=currency, **fields
        )
        _settle_deferred_constraints()
        return order.pk

    def _after_order(self, pk):
        Order = _migrate_to(AFTER).get_model("order", "Order")
        return Order.objects.get(pk=pk)

    def test_the_amount_owed_is_carried_over(self):
        pk = self._before_order(Decimal("64.17"))

        order = self._after_order(pk)

        self.assertEqual(order.amount_due.amount, Decimal("64.17"))
        self.assertEqual(order.amount_due_currency, "EUR")

    def test_a_zero_amount_owed_stays_zero(self):
        """An order deductions covered in full owes nothing."""
        pk = self._before_order(Decimal("0.00"))

        self.assertEqual(
            self._after_order(pk).amount_due.amount, Decimal("0.00")
        )

    def test_the_currency_is_carried_over(self):
        pk = self._before_order(Decimal("10.00"), currency="USD")

        self.assertEqual(self._after_order(pk).amount_due_currency, "USD")

    def test_a_null_paid_amount_takes_the_defaults(self):
        pk = self._before_order(None, currency=None)

        order = self._after_order(pk)

        self.assertEqual(order.amount_due.amount, Decimal(0))
        self.assertEqual(order.amount_due_currency, "EUR")

    def test_every_order_is_copied_not_only_the_last(self):
        first = self._before_order(Decimal("5.00"))
        second = self._before_order(Decimal("7.25"))

        Order = _migrate_to(AFTER).get_model("order", "Order")

        self.assertEqual(
            Order.objects.get(pk=first).amount_due.amount, Decimal("5.00")
        )
        self.assertEqual(
            Order.objects.get(pk=second).amount_due.amount, Decimal("7.25")
        )

    def test_the_old_columns_stay_for_the_release_still_serving(self):
        _migrate_forward()
        columns = _columns()

        self.assertIn("paid_amount", columns)
        self.assertIn("paid_amount_currency", columns)
        # Nullable, so the new code's INSERTs (which no longer name them)
        # still work.
        self.assertEqual(columns["paid_amount"][0], "YES")
        self.assertEqual(columns["paid_amount_currency"][0], "YES")

    def test_the_new_columns_are_not_null_and_default_in_the_database(self):
        _migrate_forward()
        columns = _columns()

        for name in ("amount_due", "amount_due_currency"):
            nullable, default = columns[name]
            self.assertEqual(nullable, "NO", name)
            self.assertIsNotNone(default, f"{name} has no database default")
        self.assertIn("'EUR'", columns["amount_due_currency"][1])

    def test_the_release_still_serving_can_keep_inserting_orders(self):
        """Its INSERT names ``paid_amount`` and neither new column. The
        row takes the defaults (0 and EUR); the release after this one
        re-copies it before it drops the old columns."""
        _migrate_forward()
        loader = MigrationExecutor(connection).loader
        Serving = loader.project_state([BEFORE]).apps.get_model(
            "order", "Order"
        )
        Current = loader.project_state([AFTER]).apps.get_model("order", "Order")

        pk = Serving.objects.create(
            paid_amount=Decimal("9.90"), paid_amount_currency="EUR"
        ).pk
        _settle_deferred_constraints()

        row = Current.objects.get(pk=pk)
        self.assertEqual(row.amount_due.amount, Decimal(0))
        self.assertEqual(row.amount_due_currency, "EUR")

    def test_reverse_writes_the_amount_back_to_paid_amount(self):
        Order = _migrate_to(AFTER).get_model("order", "Order")
        pk = Order.objects.create(amount_due=Decimal("12.50")).pk
        _settle_deferred_constraints()

        Order = _migrate_to(BEFORE).get_model("order", "Order")

        self.assertEqual(
            Order.objects.get(pk=pk).paid_amount.amount, Decimal("12.50")
        )
