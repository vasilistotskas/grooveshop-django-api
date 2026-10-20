"""``order`` 0087: staff ids leave ``performed_by``, shoppers stay.

Before the split every row a staff member caused (the admin's stock edit,
a restock, an adjustment) carried their PLATFORM id in ``performed_by``,
where it reads as the store's shopper with the same id. A shopper is named
only on their own reservation: reserving, releasing and converting it.
"""

from __future__ import annotations

from importlib import import_module

import pytest
from django.db import connection

from order.models import StockLog
from product.factories.product import ProductFactory

pytestmark = pytest.mark.django_db

migration = import_module("order.migrations.0087_staff_actor_ids")
SQL = next(
    op for op in migration.Migration.operations if hasattr(op, "reverse_sql")
)


def _row(product, operation, reason, actor_id):
    log = StockLog.objects.create(
        product=product,
        operation_type=operation,
        quantity_delta=1,
        stock_before=1,
        stock_after=2,
        reason=reason,
    )
    # The pre-split shape: the id written straight into ``performed_by``.
    StockLog.objects.filter(pk=log.pk).update(performed_by_id=actor_id)
    return log.pk


def _run(sql):
    with connection.cursor() as cursor:
        cursor.execute(sql)


@pytest.fixture
def rows(db):
    from user.factories.account import UserAccountFactory

    actor = UserAccountFactory().pk
    product = ProductFactory.create(num_images=0, num_reviews=0)
    staff = {
        "manual": _row(
            product,
            StockLog.OPERATION_INCREMENT,
            "Manual stock adjustment via admin panel",
            actor,
        ),
        "restock": _row(
            product, StockLog.OPERATION_INCREMENT, "Came back", actor
        ),
        "adjust": _row(
            product, StockLog.OPERATION_DECREMENT, "Stock count", actor
        ),
    }
    shopper = {
        "reserve": _row(
            product, StockLog.OPERATION_RESERVE, "Stock reserved", actor
        ),
        "release": _row(
            product, StockLog.OPERATION_RELEASE, "Reservation expired", actor
        ),
        "sale": _row(
            product,
            StockLog.OPERATION_DECREMENT,
            "Reservation 7 converted to sale for order 9",
            actor,
        ),
    }
    return actor, staff, shopper


def test_staff_rows_move_and_shopper_rows_stay(rows):
    actor, staff, shopper = rows

    _run(SQL.sql)

    for pk in staff.values():
        log = StockLog.objects.get(pk=pk)
        assert (log.performed_by_id, log.performed_by_staff_id) == (
            None,
            actor,
        )
    for pk in shopper.values():
        log = StockLog.objects.get(pk=pk)
        assert (log.performed_by_id, log.performed_by_staff_id) == (
            actor,
            None,
        )


def test_reversing_restores_the_original_rows(rows):
    actor, staff, shopper = rows

    _run(SQL.sql)
    _run(SQL.reverse_sql)

    for pk in [*staff.values(), *shopper.values()]:
        log = StockLog.objects.get(pk=pk)
        assert (log.performed_by_id, log.performed_by_staff_id) == (
            actor,
            None,
        )
