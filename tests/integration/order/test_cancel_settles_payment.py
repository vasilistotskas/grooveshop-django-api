"""A canceled order that was never paid owes nothing: its payment is settled.

Before this, ``cancel_order`` touched ``payment_status`` only when it
refunded, so every canceled COD order and every unpaid Viva order that
the 24h auto-cancel closed read "Pending" forever — 78 rows on tenant #1
on 2026-09-18 (order 240 among them). The rule lives in ``Order.save()``
so every door — the service, the admin form save, a script — shares one
save; a second save from inside the cancel cascade re-fired the whole
status transition (CI caught it: ``order_status_changed`` twice).
"""

from __future__ import annotations

from unittest import mock
from unittest.mock import Mock

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from order.enum.status import OrderStatus, PaymentStatus
from order.factories.order import OrderFactory
from order.models.order import Order
from order.services import OrderService
from order.signals import order_status_changed


@pytest.mark.django_db
class TestServicePath:
    @pytest.mark.parametrize(
        "unpaid",
        [
            PaymentStatus.PENDING,
            PaymentStatus.PROCESSING,
            PaymentStatus.FAILED,
        ],
    )
    def test_an_unpaid_order_is_settled_as_canceled(self, unpaid):
        order = OrderFactory(status=OrderStatus.PENDING, payment_status=unpaid)

        canceled, refund_info = OrderService.cancel_order(order, reason="test")

        assert canceled.status == OrderStatus.CANCELED
        assert canceled.payment_status == PaymentStatus.CANCELED
        assert refund_info is None
        assert (
            Order.objects.get(pk=order.pk).payment_status
            == PaymentStatus.CANCELED
        )

    def test_a_paid_order_is_left_to_the_refund(self):
        # COMPLETED is a settled state: the refund path owns it, and a
        # refund that is skipped or fails must not masquerade as CANCELED.
        order = OrderFactory(
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.COMPLETED,
            payment_id="",
        )

        canceled, _ = OrderService.cancel_order(
            order, reason="test", refund_payment=False
        )

        assert canceled.payment_status == PaymentStatus.COMPLETED


@pytest.mark.django_db
class TestAdminFormSavePath:
    def test_flipping_status_alone_still_settles_the_payment(self):
        order = OrderFactory(
            status=OrderStatus.PENDING, payment_status=PaymentStatus.PENDING
        )

        order.status = OrderStatus.CANCELED
        with mock.patch("order.services.OrderService.cancel_attached_shipment"):
            order.save(update_fields=["status"])

        assert (
            Order.objects.get(pk=order.pk).payment_status
            == PaymentStatus.CANCELED
        )

    def test_the_transition_fires_once(self):
        # The settle rides the SAME save. A second save from inside the
        # cascade ran while ``_original_status`` still held the old
        # status and re-fired the transition — emails included.
        order = OrderFactory(
            status=OrderStatus.PENDING, payment_status=PaymentStatus.PENDING
        )
        receiver = Mock()
        order_status_changed.connect(receiver)
        try:
            order.status = OrderStatus.CANCELED
            with mock.patch(
                "order.services.OrderService.cancel_attached_shipment"
            ):
                order.save()
        finally:
            order_status_changed.disconnect(receiver)

        receiver.assert_called_once()

    def test_any_other_transition_leaves_the_payment_alone(self):
        order = OrderFactory(
            status=OrderStatus.PENDING, payment_status=PaymentStatus.PENDING
        )

        order.status = OrderStatus.PROCESSING
        order.save()

        assert (
            Order.objects.get(pk=order.pk).payment_status
            == PaymentStatus.PENDING
        )


def test_settle_never_touches_a_settled_state():
    for settled in (
        PaymentStatus.COMPLETED,
        PaymentStatus.REFUNDED,
        PaymentStatus.PARTIALLY_REFUNDED,
        PaymentStatus.CANCELED,
    ):
        order = Order(payment_status=settled)
        assert order.settle_payment_on_cancel() is False
        assert order.payment_status == settled

    order = Order(payment_status=PaymentStatus.PENDING)
    assert order.settle_payment_on_cancel() is True
    assert order.payment_status == PaymentStatus.CANCELED


@pytest.mark.django_db(transaction=True)
def test_backfill_settles_only_canceled_unpaid_rows():
    executor = MigrationExecutor(connection)
    settle = (
        executor.loader.get_migration(
            "order", "0057_settle_canceled_unpaid_orders"
        )
        .operations[0]
        .code
    )

    stale = OrderFactory(
        status=OrderStatus.CANCELED, payment_status=PaymentStatus.PENDING
    )
    failed = OrderFactory(
        status=OrderStatus.CANCELED, payment_status=PaymentStatus.FAILED
    )
    refunded = OrderFactory(
        status=OrderStatus.CANCELED, payment_status=PaymentStatus.REFUNDED
    )
    live = OrderFactory(
        status=OrderStatus.PENDING, payment_status=PaymentStatus.PENDING
    )

    settle(executor.loader.project_state().apps, connection.schema_editor())

    statuses = {
        o.pk: Order.objects.get(pk=o.pk).payment_status
        for o in (stale, failed, refunded, live)
    }
    assert statuses[stale.pk] == PaymentStatus.CANCELED
    assert statuses[failed.pk] == PaymentStatus.CANCELED
    assert statuses[refunded.pk] == PaymentStatus.REFUNDED
    assert statuses[live.pk] == PaymentStatus.PENDING
