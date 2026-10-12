"""``0071_order_drop_paid_amount_columns`` re-copies, then drops ``paid_amount``.

Drives the real ``MigrationExecutor`` across the migration, in both
directions (the same harness as ``test_migration_amount_due``).
PostgreSQL DDL is transactional, so the ``TestCase`` transaction undoes the
schema changes and leaves the worker's database at the latest migration
again.

The rows the migration has to rescue are the ones the release before
``0070`` wrote while it still served: its INSERT named ``paid_amount`` and
not ``amount_due``, so ``amount_due`` kept its database default (0). The
state at ``0070`` has no ``paid_amount`` field, so those rows are written
with the SQL that release sent.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase

BEFORE = ("order", "0070_order_amount_due")
AFTER = ("order", "0071_order_drop_paid_amount_columns")

LEGACY_COLUMNS = ("paid_amount", "paid_amount_currency")


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


def _columns() -> dict[str, tuple[str, str | None, int | None]]:
    """``order_order``'s columns: name -> (is_nullable, data_type,
    numeric_precision)."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT column_name, is_nullable, data_type, numeric_precision "
            "FROM information_schema.columns "
            "WHERE table_schema = current_schema() "
            "AND table_name = 'order_order'"
        )
        return {
            name: (nullable, data_type, precision)
            for name, nullable, data_type, precision in cursor
        }


class TestPaidAmountContract(TestCase):
    def _served_by_the_previous_release(self, paid, currency="EUR"):
        """An order its INSERT wrote: ``paid_amount`` set, ``amount_due``
        left at the database default."""
        Order = _migrate_to(BEFORE).get_model("order", "Order")
        pk = Order.objects.create().pk
        _settle_deferred_constraints()
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE order_order "
                "SET paid_amount = %s, paid_amount_currency = %s "
                "WHERE id = %s",
                [paid, currency, pk],
            )
        return pk

    def _written_by_the_current_release(self, due, paid=None):
        Order = _migrate_to(BEFORE).get_model("order", "Order")
        pk = Order.objects.create(amount_due=due).pk
        _settle_deferred_constraints()
        if paid is not None:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE order_order SET paid_amount = %s WHERE id = %s",
                    [paid, pk],
                )
        return pk

    def _after(self, pk):
        Order = _migrate_to(AFTER).get_model("order", "Order")
        return Order.objects.get(pk=pk)

    def test_an_order_the_previous_release_wrote_keeps_what_it_owed(self):
        pk = self._served_by_the_previous_release(Decimal("64.17"))

        order = self._after(pk)

        self.assertEqual(order.amount_due.amount, Decimal("64.17"))
        self.assertEqual(order.amount_due_currency, "EUR")

    def test_its_currency_is_carried_over(self):
        pk = self._served_by_the_previous_release(
            Decimal("10.00"), currency="USD"
        )

        self.assertEqual(self._after(pk).amount_due_currency, "USD")

    def test_a_null_currency_falls_back_to_eur(self):
        pk = self._served_by_the_previous_release(
            Decimal("10.00"), currency=None
        )

        order = self._after(pk)

        self.assertEqual(order.amount_due.amount, Decimal("10.00"))
        self.assertEqual(order.amount_due_currency, "EUR")

    def test_every_such_order_is_rescued_not_only_the_last(self):
        first = self._served_by_the_previous_release(Decimal("5.00"))
        second = self._served_by_the_previous_release(Decimal("7.25"))

        Order = _migrate_to(AFTER).get_model("order", "Order")

        self.assertEqual(
            Order.objects.get(pk=first).amount_due.amount, Decimal("5.00")
        )
        self.assertEqual(
            Order.objects.get(pk=second).amount_due.amount, Decimal("7.25")
        )

    def test_an_amount_the_current_release_wrote_is_never_overwritten(self):
        """``amount_due`` is the truth once it is non-zero, whatever the
        stale ``paid_amount`` beside it says."""
        pk = self._written_by_the_current_release(
            Decimal("30.00"), paid=Decimal("99.00")
        )

        self.assertEqual(self._after(pk).amount_due.amount, Decimal("30.00"))

    def test_an_order_that_owes_nothing_stays_at_zero(self):
        zero = self._served_by_the_previous_release(Decimal("0.00"))
        no_value = self._written_by_the_current_release(Decimal("0.00"))

        Order = _migrate_to(AFTER).get_model("order", "Order")

        self.assertEqual(Order.objects.get(pk=zero).amount_due.amount, 0)
        self.assertEqual(Order.objects.get(pk=no_value).amount_due.amount, 0)

    def test_the_legacy_columns_are_dropped_and_the_new_ones_stay(self):
        _migrate_to(BEFORE)
        self.assertTrue(set(LEGACY_COLUMNS) <= set(_columns()))

        _migrate_to(AFTER)
        columns = _columns()

        for name in LEGACY_COLUMNS:
            self.assertNotIn(name, columns)
        self.assertIn("amount_due", columns)
        self.assertIn("amount_due_currency", columns)

    def test_reverse_re_adds_both_columns_nullable(self):
        _migrate_to(AFTER)
        self.assertFalse(set(LEGACY_COLUMNS) & set(_columns()))

        _migrate_to(BEFORE)
        columns = _columns()

        self.assertEqual(columns["paid_amount"], ("YES", "numeric", 11))
        self.assertEqual(
            columns["paid_amount_currency"][:2], ("YES", "character varying")
        )

    def test_reverse_leaves_the_re_added_columns_empty(self):
        Order = _migrate_to(AFTER).get_model("order", "Order")
        pk = Order.objects.create(amount_due=Decimal("12.50")).pk
        _settle_deferred_constraints()

        _migrate_to(BEFORE)

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT paid_amount, paid_amount_currency "
                "FROM order_order WHERE id = %s",
                [pk],
            )
            self.assertEqual(cursor.fetchone(), (None, None))
