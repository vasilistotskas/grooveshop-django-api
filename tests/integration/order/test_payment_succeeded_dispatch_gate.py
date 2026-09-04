"""A courier shipment is only minted for an order that still has a
delivery ahead of it.

``handle_payment_succeeded`` returns early for a CANCELED order because
minting a voucher for a dead order is wrong. An order that already
SHIPPED, was DELIVERED, RETURNED or REFUNDED is equally done with its
voucher, but the dispatch below the status block was not gated at all —
only the PENDING -> PROCESSING transition was. Its own comment claimed
otherwise.

A duplicate provider success event reaches this: the idempotency flag is
keyed on the event id, so a second event for the same payment intent
(``charge.succeeded`` after ``payment_intent.succeeded``) is not deduped
by it.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from order.enum.status import OrderStatus, PaymentStatus
from order.factories.order import OrderFactory
from order.models.order import Order
from order.services import OrderService

pytestmark = pytest.mark.django_db


def _order(status, payment_status=PaymentStatus.COMPLETED):
    order = OrderFactory(num_order_items=0)
    Order.objects.filter(pk=order.pk).update(
        status=status,
        payment_status=payment_status,
        payment_id="pi_dispatch_gate",
        metadata={},
    )
    order.refresh_from_db()
    return order


def _run():
    return OrderService.handle_payment_succeeded("pi_dispatch_gate")


@pytest.mark.parametrize(
    "status",
    [
        OrderStatus.SHIPPED,
        OrderStatus.DELIVERED,
        OrderStatus.RETURNED,
        OrderStatus.REFUNDED,
    ],
)
def test_a_finished_order_mints_no_new_shipment(status):
    _order(status)

    with patch(
        "shipping.services.ShippingService.dispatch_create_shipment_task"
    ) as dispatch:
        _run()

    dispatch.assert_not_called()


@pytest.mark.parametrize(
    "status", [OrderStatus.PENDING, OrderStatus.PROCESSING]
)
def test_an_order_awaiting_delivery_still_dispatches(status):
    _order(status, payment_status=PaymentStatus.PENDING)

    with (
        patch(
            "shipping.services.ShippingService.dispatch_create_shipment_task"
        ) as dispatch,
        patch(
            "order.signals.handlers.send_order_confirmation_email.apply_async"
        ),
    ):
        _run()

    dispatch.assert_called_once()
