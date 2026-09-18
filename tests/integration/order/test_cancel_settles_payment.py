"""A canceled order that was never paid owes nothing: its payment is settled.

Before this, ``cancel_order`` touched ``payment_status`` only when it
refunded, so every canceled COD order and every unpaid Viva order that
the 24h auto-cancel closed read "Pending" forever — 78 rows on tenant #1
on 2026-09-18 (order 240 among them). Both doors are covered: the
service, and the admin form save that flips ``status`` on its own.
"""

from __future__ import annotations

from unittest import mock

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from order.enum.status import OrderStatus, PaymentStatus
from order.factories.order import OrderFactory
from order.models.order import Order
from order.services import OrderService, settle_unpaid_payment_on_cancel


@pytest.mark.django_db
class TestServicePath:
    @pytest.mark.parametrize(
        "unpaid",
        [PaymentStatus.PENDING, PaymentStatus.PROCESSING, PaymentStatus.FAILED],
    )
    def test_an_unpaid_order_is_settled_as_canceled(self, unpaid):
        order = OrderFactory(status=OrderStatus.PENDING, payment_status=unpaid)

        # The service settles it in its OWN save; the signal cascade is
        # the safety net for the admin path, so it is silenced here to
        # prove the service does not lean on it.
        with mock.patch(
            "order.signals.handlers.settle_unpaid_payment_on_cancel",
            return_value=False,
        ):
            canceled, refund_info = OrderService.cancel_order(
                order, reason="test"
            )

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
            order.save()

        assert (
            Order.objects.get(pk=order.pk).payment_status
            == PaymentStatus.CANCELED
        )


def test_helper_never_touches_a_settled_state():
    for settled in (
        PaymentStatus.COMPLETED,
        PaymentStatus.REFUNDED,
        PaymentStatus.PARTIALLY_REFUNDED,
        PaymentStatus.CANCELED,
    ):
        order = Order(payment_status=settled)
        assert settle_unpaid_payment_on_cancel(order) is False
        assert order.payment_status == settled

    order = Order(payment_status=PaymentStatus.PENDING)
    assert settle_unpaid_payment_on_cancel(order) is True
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
