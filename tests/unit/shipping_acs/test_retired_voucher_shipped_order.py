"""A voucher retired after its order shipped is a fact about the voucher.

A CANCELED ACS shipment (the admin "Retire selected shipments" action, a
voucher cancel) means the parcel never left only while the order has not
shipped. Once the order is SHIPPED the order keeps its status: it cannot
be canceled, and the cancel raised ``OrderNotCancelableError`` every time
``manage.py reconcile_acs_order_status --apply`` met the pair.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest
from django.core.management import call_command

from order.enum.status import OrderStatus
from order.exceptions import OrderCancellationError
from order.factories.order import OrderFactory
from shipping_acs.enum.shipment_state import AcsShipmentState
from shipping_acs.factories import AcsShipmentFactory
from shipping_acs.services import AcsService

pytestmark = pytest.mark.django_db


def _run_replay(**options):
    from shipping_acs.management.commands import reconcile_acs_order_status

    with patch.object(
        reconcile_acs_order_status.Command,
        "get_target_tenants",
        return_value=[None],
    ):
        call_command("reconcile_acs_order_status", **options)


@pytest.fixture(autouse=True)
def _acs_configured():
    with patch("shipping_acs.config.is_configured", return_value=True):
        yield


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (OrderStatus.PENDING, "CANCELED"),
        (OrderStatus.PROCESSING, "CANCELED"),
        (OrderStatus.SHIPPED, None),
        (OrderStatus.DELIVERED, None),
        (OrderStatus.RETURNED, None),
    ],
)
def test_a_canceled_shipment_cancels_only_an_order_that_has_not_shipped(
    status, expected
):
    assert (
        AcsService.order_status_for_shipment_state(
            AcsShipmentState.CANCELED, status.value
        )
        == expected
    )


def test_a_retired_voucher_leaves_a_shipped_order_alone(caplog):
    order = OrderFactory(status=OrderStatus.SHIPPED)

    with caplog.at_level(logging.WARNING, logger="shipping_acs.services"):
        AcsService._apply_order_status_transition(
            order, AcsShipmentState.CANCELED
        )

    order.refresh_from_db()
    assert order.status == OrderStatus.SHIPPED
    warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and r.name == "shipping_acs.services"
    ]
    assert len(warnings) == 1
    assert str(order.id) in warnings[0].getMessage()


def test_the_replay_treats_a_retired_voucher_on_a_shipped_order_as_agreed(
    capsys,
):
    order = OrderFactory(status=OrderStatus.SHIPPED)
    AcsShipmentFactory(
        order=order,
        voucher_no="9800000612",
        shipment_state=AcsShipmentState.CANCELED,
    )

    _run_replay(orders=[order.id], apply=True)

    order.refresh_from_db()
    captured = capsys.readouterr()
    assert order.status == OrderStatus.SHIPPED
    assert "agrees with its order status" in captured.out
    assert captured.err == ""


def test_a_cancel_that_failed_on_the_way_still_propagates():
    """Only a refusal by status is dropped; a failed cancel is retried."""
    order = OrderFactory(status=OrderStatus.PROCESSING)

    with (
        patch(
            "order.services.OrderService.cancel_for_dead_shipment",
            side_effect=OrderCancellationError(order.id, "lock timed out"),
        ),
        pytest.raises(OrderCancellationError),
    ):
        AcsService._apply_order_status_transition(
            order, AcsShipmentState.CANCELED
        )
