"""``AddUniqueConstraintAdaptively``, run against the real ``order_order``.

The operation ships ``order.0069_order_payment_id_unique`` onto a live
table, so what matters is what it does to data that already exists:

* duplicates make it fail with a message that names them, BEFORE anything
  is built (a concurrent unique build that meets one leaves an INVALID
  index behind);
* rows that merely have no payment id (``''`` and NULL) are never
  duplicates of each other;
* a clean table gets a valid, unique, partial index, built CONCURRENTLY
  when no transaction is open and with a plain build inside one;
* the router and reversal behave like ``AddIndexAdaptively``'s.

The test database already carries the index (it is built by migrating),
so each test drops it first and the operation puts it back.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.apps import apps as global_apps
from django.db import IntegrityError, connection, transaction
from django.db.migrations.state import ProjectState
from django.test import override_settings

from core.db.migration_operations import AddUniqueConstraintAdaptively
from order.models.order import Order

INDEX = "order_payment_id_unique"

_CONSTRAINT = next(c for c in Order._meta.constraints if c.name == INDEX)

_ROW = {
    "email": "x@example.com",
    "first_name": "A",
    "last_name": "B",
    "street": "S",
    "street_number": "1",
    "city": "C",
    "zipcode": "12345",
    "phone": "+306900000000",
}


def _index_state() -> tuple[bool, bool] | None:
    """``(valid, unique)`` of the index, or None when it does not exist."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT x.indisvalid, x.indisunique FROM pg_index x "
            "JOIN pg_class i ON i.oid = x.indexrelid "
            "JOIN pg_namespace n ON n.oid = i.relnamespace "
            "WHERE i.relname = %s AND n.nspname = current_schema()",
            [INDEX],
        )
        return cursor.fetchone()


def _drop_index() -> None:
    with connection.cursor() as cursor:
        cursor.execute(f'DROP INDEX IF EXISTS "{INDEX}"')


def _run(operation=None, *, forwards=True) -> None:
    if connection.in_atomic_block:
        # Rows inserted earlier in this same (test) transaction leave
        # deferred foreign-key trigger events pending on the table, and
        # Postgres refuses to CREATE INDEX over them. A real caller in a
        # transaction is provisioning a tenant schema whose table is empty.
        with connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    state = ProjectState.from_apps(global_apps)
    operation = operation or AddUniqueConstraintAdaptively("order", _CONSTRAINT)
    with connection.schema_editor(atomic=False) as editor:
        if forwards:
            operation.database_forwards("order", editor, state, state)
        else:
            operation.database_backwards("order", editor, state, state)


def _orders(*payment_ids) -> None:
    Order.objects.bulk_create(
        [Order(payment_id=value, **_ROW) for value in payment_ids]
    )


@pytest.mark.django_db
class TestInsideATransaction:
    """The test's own transaction is open: the plain-build branch."""

    def test_existing_duplicates_fail_the_migration_naming_them(self):
        _drop_index()
        _orders("pi_dup_a", "pi_dup_a", "pi_dup_b", "pi_dup_b", "pi_dup_b")
        _orders("pi_fine")

        with pytest.raises(IntegrityError) as caught:
            _run()

        message = str(caught.value)
        assert INDEX in message
        assert "pi_dup_b" in message and "3 rows" in message, message
        assert "pi_dup_a" in message and "2 rows" in message, message
        assert "pi_fine" not in message, "a unique value is not a duplicate"
        assert "Nothing was changed" in message
        assert _index_state() is None, (
            "the failure must come BEFORE the build: no index, valid or "
            "otherwise, may be left behind"
        )

    def test_rows_without_a_payment_id_are_not_duplicates(self):
        _drop_index()
        _orders("", "", None, None, "pi_only_one")

        _run()

        assert _index_state() == (True, True)

    def test_a_clean_table_gets_a_valid_unique_partial_index(self):
        _drop_index()
        _orders("pi_a", "pi_b")

        _run()

        assert _index_state() == (True, True)
        with pytest.raises(IntegrityError), transaction.atomic():
            _orders("pi_a")
        # Still repeatable where there is nothing to be unique about.
        _orders("", "", None)

    def test_running_it_twice_is_a_no_op(self):
        _drop_index()

        _run()
        _run()

        assert _index_state() == (True, True)

    def test_reversal_drops_the_index(self):
        _drop_index()
        _run()

        _run(forwards=False)

        assert _index_state() is None

    def test_the_router_is_honoured(self):
        class _DenyEverything:
            def allow_migrate(self, db, app_label, **hints):
                return False

        _drop_index()
        _orders("pi_dup", "pi_dup")

        with override_settings(DATABASE_ROUTERS=[_DenyEverything()]):
            _run()

        assert _index_state() is None, (
            "Raw SQL bypasses the router, so the operation has to make "
            "the check Django's own operations make; without it, "
            "migrating the public schema reaches a tenant app's table."
        )

    def test_a_constraint_without_a_condition_is_refused(self):
        """Django builds that one as ``ALTER TABLE ... ADD CONSTRAINT``,
        so there is no index statement to make concurrent."""
        from django.db.models import UniqueConstraint

        operation = AddUniqueConstraintAdaptively(
            "order", UniqueConstraint(fields=["uuid"], name="probe_unique")
        )

        with pytest.raises(ValueError, match="CREATE UNIQUE INDEX"):
            _run(operation)


@pytest.mark.django_db(transaction=True)
def test_outside_a_transaction_the_index_is_built_concurrently():
    """The PreSync ``migrate_schemas`` job has no transaction open.

    ``CREATE INDEX CONCURRENTLY`` cannot run inside one, so the real
    statement is only observable here. The index is dropped first and the
    operation puts it back, so the schema ends as it began.
    """
    executed: list[str] = []

    def record(execute, sql, params, many, context):
        executed.append(sql)
        return execute(sql, params, many, context)

    _drop_index()
    try:
        with connection.execute_wrapper(record):
            _run()

        creates = [sql for sql in executed if "CREATE UNIQUE INDEX" in sql]
        assert len(creates) == 1, executed
        assert creates[0].startswith(
            f'CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS "{INDEX}" '
        ), creates[0]
        assert 'ON "order_order" ("payment_id") WHERE' in creates[0]
        assert _index_state() == (True, True)
    finally:
        if _index_state() is None:
            _run()


@pytest.mark.django_db(transaction=True)
def test_a_build_that_fails_after_the_count_leaves_no_index_behind():
    """The old pods keep inserting while the index is built, so a duplicate
    can arrive after the up-front count. The concurrent build then fails
    half way, and Postgres leaves its INVALID index in place. The failed
    build drops it before re-raising, so nothing lingers (an invalid index
    still costs every write) and a retry after the data is fixed builds a
    valid one.

    The late duplicate is simulated by blinding the count; the duplicates
    are committed rows and the build is the real ``CONCURRENTLY`` one.
    """
    _drop_index()
    try:
        _orders("pi_late", "pi_late")
        with (
            patch.object(
                AddUniqueConstraintAdaptively,
                "_refuse_duplicates",
                lambda *args, **kwargs: None,
            ),
            pytest.raises(IntegrityError),
        ):
            _run()

        assert _index_state() is None, (
            "the failed build left its INVALID index behind"
        )

        keep = Order.objects.filter(payment_id="pi_late").order_by("pk").first()
        Order.objects.filter(payment_id="pi_late").exclude(pk=keep.pk).update(
            payment_id=""
        )
        _run()

        assert _index_state() == (True, True)
    finally:
        Order.objects.all_with_deleted().filter(payment_id="pi_late").update(
            payment_id=""
        )
        if _index_state() is None:
            _run()
