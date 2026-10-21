"""``0090_order_drop_reminder_columns`` drops the counters 0084 hid from state.

Drives the real ``MigrationExecutor`` across the migration in both
directions (the same harness as ``test_migration_amount_due_contract``).
PostgreSQL DDL is transactional, so the ``TestCase`` transaction undoes the
schema changes and leaves the worker's database at the latest migration
again.
"""

from __future__ import annotations

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase

BEFORE = ("order", "0089_remove_german_translations")
AFTER = ("order", "0090_order_drop_reminder_columns")

REMINDER_COLUMNS = ("reminder_count", "last_reminder_sent_at")


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


def _columns() -> dict[str, tuple[str, str | None, str | None]]:
    """``order_order``'s columns: name -> (is_nullable, data_type,
    column_default)."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT column_name, is_nullable, data_type, column_default "
            "FROM information_schema.columns "
            "WHERE table_schema = current_schema() "
            "AND table_name = 'order_order'"
        )
        return {
            name: (nullable, data_type, default)
            for name, nullable, data_type, default in cursor
        }


class TestReminderColumnsContract(TestCase):
    def test_the_columns_are_dropped_and_the_rest_stays(self):
        _migrate_to(BEFORE)
        self.assertTrue(set(REMINDER_COLUMNS) <= set(_columns()))

        _migrate_to(AFTER)
        columns = _columns()

        for name in REMINDER_COLUMNS:
            self.assertNotIn(name, columns)
        self.assertIn("amount_due", columns)

    def test_an_order_survives_the_drop(self):
        Order = _migrate_to(BEFORE).get_model("order", "Order")
        pk = Order.objects.create().pk
        _settle_deferred_constraints()

        Order = _migrate_to(AFTER).get_model("order", "Order")

        self.assertTrue(Order.objects.filter(pk=pk).exists())

    def test_reverse_restores_the_shape_0084_left(self):
        Order = _migrate_to(AFTER).get_model("order", "Order")
        pk = Order.objects.create().pk
        _settle_deferred_constraints()

        _migrate_to(BEFORE)
        columns = _columns()

        self.assertEqual(columns["reminder_count"][:2], ("NO", "smallint"))
        self.assertEqual(columns["reminder_count"][2], "0")
        self.assertEqual(
            columns["last_reminder_sent_at"][:2],
            ("YES", "timestamp with time zone"),
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT reminder_count, last_reminder_sent_at "
                "FROM order_order WHERE id = %s",
                [pk],
            )
            self.assertEqual(cursor.fetchone(), (0, None))
