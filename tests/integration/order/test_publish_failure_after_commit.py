"""A broker outage after the order committed does not undo the order.

``send_order_confirmation_email.delay`` and the carrier adapters'
``apply_async`` ran inside ``on_commit`` hooks with no guard: Celery raises
``kombu.exceptions.OperationalError`` when the broker is unreachable, the
hook raised after the commit, the shopper got a 500 for an order that
existed, and every later hook of the transaction was dropped. Each of these
tests breaks the broker and asserts the work that was committed stays, the
dispatch is kept as a ``DeferredDispatch``, and nothing reaches the caller.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from kombu.exceptions import OperationalError

from order.enum.status import OrderStatus, PaymentStatus
from order.factories.order import OrderFactory
from order.models.history import OrderHistory
from order.models.order import Order
from order.signals import order_created
from order.tasks import (
    send_admin_new_order_email,
    send_order_confirmation_email,
)
from pay_way.factories import PayWayFactory
from shipping.factories import ShippingProviderFactory
from shipping.services import ShippingService
from shipping_acs.tasks import create_acs_voucher_for_order
from shipping_boxnow.tasks import create_boxnow_shipment_for_order
from tenant.models import DeferredDispatch

pytestmark = pytest.mark.django_db

BROKER_DOWN = OperationalError("broker unreachable")


def _task_names() -> set[str]:
    return set(DeferredDispatch.objects.values_list("task_name", flat=True))


def test_a_broker_failure_after_the_order_commits_keeps_the_order_flow():
    order = OrderFactory(
        status=OrderStatus.PENDING,
        payment_status=PaymentStatus.PENDING,
        num_order_items=0,
    )
    order.pay_way = PayWayFactory.create_offline_payment()
    order.save(update_fields=["pay_way"])

    with (
        patch.object(
            send_order_confirmation_email,
            "apply_async",
            side_effect=BROKER_DOWN,
        ),
        patch.object(
            send_admin_new_order_email, "apply_async", side_effect=BROKER_DOWN
        ),
    ):
        order_created.send(sender=Order, order=order)

    assert _task_names() == {
        send_order_confirmation_email.name,
        send_admin_new_order_email.name,
    }
    # The hooks after the publishes still ran.
    assert OrderHistory.objects.filter(
        order=order, new_value={"note": "Order created"}
    ).exists()


@pytest.mark.parametrize(
    ("code", "task"),
    [
        ("acs", create_acs_voucher_for_order),
        ("boxnow", create_boxnow_shipment_for_order),
    ],
)
def test_a_paid_order_keeps_its_voucher_dispatch_when_the_broker_is_down(
    code, task
):
    order = OrderFactory(
        status=OrderStatus.PROCESSING,
        payment_status=PaymentStatus.COMPLETED,
        num_order_items=0,
    )
    provider = ShippingProviderFactory(
        code=code,
        is_active=True,
        supports_home_delivery=True,
        supports_pickup_point=True,
    )
    order.shipping_provider = provider
    order.shipping_kind = "pickup_point"
    order.save(update_fields=["shipping_provider", "shipping_kind"])

    with patch.object(task, "apply_async", side_effect=BROKER_DOWN):
        ShippingService.dispatch_create_shipment_task(order)

    row = DeferredDispatch.objects.get()
    assert row.task_name == task.name
    assert row.args == [order.id]
