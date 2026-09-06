"""The expiry sweep must drain in bounded transactions.

It used to load every expired reservation at once, put every id into one
`id__in=[...]`, and build every `StockLog` in Python with that
transaction still open. The connection carries `statement_timeout=30000`
and `idle_in_transaction_session_timeout=10000`, so after an outage the
backlog could exceed either — rolling the whole sweep back, leaving
`consumed` False, and handing the next run a larger batch. The caller
swallows the exception and logs success, so the backlog never drains and
nothing says so.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from order.models.stock_log import StockLog
from order.models.stock_reservation import StockReservation
from order.stock import StockManager
from product.factories.product import ProductFactory
from product.models.product import Product

pytestmark = pytest.mark.django_db


def _expired(product, count):
    past = timezone.now() - timedelta(hours=1)
    StockReservation.objects.bulk_create(
        [
            StockReservation(
                product=product,
                quantity=1,
                session_id=f"expired-{index}",
                expires_at=past,
                consumed=False,
            )
            for index in range(count)
        ]
    )


def test_a_backlog_larger_than_one_batch_still_fully_drains(monkeypatch):
    monkeypatch.setattr(StockManager, "CLEANUP_BATCH_SIZE", 3)
    product = ProductFactory(num_images=0, num_reviews=0, stock=100)
    _expired(product, 7)

    released = StockManager.cleanup_expired_reservations()

    assert released == 7, "the sweep stopped after the first batch"
    assert not StockReservation.objects.filter(
        consumed=False, expires_at__lt=timezone.now()
    ).exists()


def test_every_released_reservation_still_gets_its_audit_row(monkeypatch):
    from order.models.stock_log import StockLog

    monkeypatch.setattr(StockManager, "CLEANUP_BATCH_SIZE", 2)
    product = ProductFactory(num_images=0, num_reviews=0, stock=100)
    _expired(product, 5)

    StockManager.cleanup_expired_reservations()

    assert (
        StockLog.objects.filter(
            product=product, operation_type=StockLog.OPERATION_RELEASE
        ).count()
        == 5
    )


def test_an_empty_backlog_is_a_no_op():
    assert StockManager.cleanup_expired_reservations() == 0


@pytest.mark.django_db(transaction=True)
def test_an_earlier_batch_survives_a_later_batch_failing(monkeypatch):
    """Each batch must commit on its own — the point of batching.

    `transaction=True` is required and is not incidental: under the
    default marker every test runs inside one outer transaction, so an
    inner `atomic()` is a savepoint whether or not the method is
    decorated, and the two behaviours are indistinguishable. Only a real
    commit can show the difference.

    The method carried `@transaction.atomic`. Django opens a transaction
    at the OUTERMOST atomic block and inner blocks only create
    savepoints — `Atomic.__exit__` releases a savepoint while
    `connection.in_atomic_block`, and calls `connection.commit()` only
    when it is not. So no batch committed independently, every row lock
    was held until the method returned, and one late failure discarded
    all the earlier work.
    """
    monkeypatch.setattr(StockManager, "CLEANUP_BATCH_SIZE", 2)
    product = ProductFactory(num_images=0, num_reviews=0, stock=100)
    _expired(product, 6)

    calls = {"n": 0}
    real_bulk_create = StockLog.objects.bulk_create

    def _fail_on_the_third_batch(objs, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("statement timeout")
        return real_bulk_create(objs, *args, **kwargs)

    monkeypatch.setattr(
        StockLog.objects, "bulk_create", _fail_on_the_third_batch
    )

    with pytest.raises(RuntimeError):
        StockManager.cleanup_expired_reservations()

    drained = StockReservation.objects.filter(consumed=True).count()
    assert drained == 4, (
        f"only {drained} reservations survived the failure — the earlier "
        f"batches were rolled back with the failing one, so the sweep "
        f"makes no progress against a backlog it cannot finish"
    )


def test_a_concurrently_converted_reservation_is_neither_logged_nor_counted(
    monkeypatch,
):
    """The sweep must report what it released, not what it intended to.

    The batch is selected without a lock, so a reservation can be
    converted to a sale before the sweep gets to it. The conditional
    UPDATE correctly skipped such a row — but the audit log and the
    returned count were still built from the stale batch, so the task
    logged a release that never happened and a total that included it.
    """
    monkeypatch.setattr(StockManager, "CLEANUP_BATCH_SIZE", 10)
    product = ProductFactory(num_images=0, num_reviews=0, stock=100)
    _expired(product, 3)
    victim = StockReservation.objects.order_by("id").first()

    real_lock = StockManager  # marker for readability
    assert real_lock is not None

    original = Product.objects.select_for_update

    def _convert_one_then_lock(*args, **kwargs):
        # Stands in for another worker converting the reservation to a
        # sale in the window between the unlocked SELECT and the locks.
        StockReservation.objects.filter(id=victim.id).update(consumed=True)
        return original(*args, **kwargs)

    monkeypatch.setattr(
        Product.objects, "select_for_update", _convert_one_then_lock
    )

    released = StockManager.cleanup_expired_reservations()

    assert released == 2, (
        f"reported {released} releases for 2 reservations actually "
        f"released — the converted one was counted from the stale batch"
    )
    assert not StockLog.objects.filter(
        reason__contains=f"Expired reservation {victim.id} "
    ).exists(), "a release was logged for a reservation nobody released"
