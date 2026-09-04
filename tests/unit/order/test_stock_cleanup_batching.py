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

from order.models.stock_reservation import StockReservation
from order.stock import StockManager
from product.factories.product import ProductFactory

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
