"""A voucher retired after its order shipped is a fact about the voucher.

A CANCELED ACS shipment (the admin "Retire selected shipments" action, a
voucher cancel) means the parcel never left only while the order has not
shipped. Once the order is SHIPPED the order keeps its status: it cannot
be canceled, and the cancel raised ``OrderNotCancelableError`` every time
``manage.py reconcile_acs_order_status --apply`` met the pair.
"""

from __future__ import annotations

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


@pytest.mark.parametrize("apply", [False, True], ids=["dry-run", "apply"])
def test_the_replay_reports_a_retired_voucher_on_a_shipped_order(apply, capsys):
    """Not drift to repair, but a fact a person should see: the order is
    left alone and the pair is listed in the dry run and under --apply."""
    order = OrderFactory(status=OrderStatus.SHIPPED)
    AcsShipmentFactory(
        order=order,
        voucher_no="9800000612",
        shipment_state=AcsShipmentState.CANCELED,
    )

    _run_replay(orders=[order.id], apply=apply)

    order.refresh_from_db()
    captured = capsys.readouterr()
    assert order.status == OrderStatus.SHIPPED
    assert f"order {order.id}: SHIPPED, but shipment 9800000612" in captured.out
    assert "retired on an order that already shipped" in captured.out
    assert "-> " not in captured.out, "nothing was transitioned"
    assert captured.err == ""


def test_a_retired_voucher_on_a_processing_order_is_drift_not_a_notice(
    capsys,
):
    """An order that has not shipped is canceled properly: it is drift to
    repair, never listed as left alone."""
    order = OrderFactory(status=OrderStatus.PROCESSING)
    AcsShipmentFactory(
        order=order,
        voucher_no="9800000613",
        shipment_state=AcsShipmentState.CANCELED,
    )

    _run_replay(orders=[order.id])

    out = capsys.readouterr().out
    assert f"order {order.id}: PROCESSING -> CANCELED" in out
    assert "1 order(s) disagree" in out
    assert "retired on an order that already shipped" not in out
    assert "but shipment" not in out


def test_a_retired_voucher_on_a_delivered_order_is_not_reported(capsys):
    order = OrderFactory(status=OrderStatus.DELIVERED)
    AcsShipmentFactory(
        order=order,
        voucher_no="9800000614",
        shipment_state=AcsShipmentState.CANCELED,
    )

    _run_replay(orders=[order.id])

    out = capsys.readouterr().out
    assert "agrees with its order status" in out
    assert "but shipment" not in out


def test_the_replay_summary_is_true_for_every_combination(capsys):
    """Drift or not, retired-on-shipped or not: four runs, four summaries."""
    shipped = OrderFactory(status=OrderStatus.SHIPPED)
    AcsShipmentFactory(
        order=shipped,
        voucher_no="9800000615",
        shipment_state=AcsShipmentState.CANCELED,
    )
    stranded = OrderFactory(status=OrderStatus.PROCESSING)
    AcsShipmentFactory(
        order=stranded,
        voucher_no="9800000616",
        shipment_state=AcsShipmentState.RETURNED,
    )
    quiet = OrderFactory(status=OrderStatus.DELIVERED)
    AcsShipmentFactory(
        order=quiet,
        voucher_no="9800000617",
        shipment_state=AcsShipmentState.DELIVERED,
    )

    # Neither.
    _run_replay(orders=[quiet.id])
    out = capsys.readouterr().out
    assert "every terminal ACS shipment agrees" in out
    assert "retired on an order" not in out
    assert "disagree" not in out

    # Retired on a shipped order only: listed, and no claim of agreement.
    _run_replay(orders=[shipped.id])
    out = capsys.readouterr().out
    assert "1 voucher(s) retired on an order that already shipped" in out
    assert "no order disagrees with a terminal shipment state" in out
    assert "every terminal ACS shipment agrees" not in out
    assert "disagree with a terminal" not in out

    # Drift only.
    _run_replay(orders=[stranded.id])
    out = capsys.readouterr().out
    assert "1 order(s) disagree with a terminal shipment state" in out
    assert "retired on an order" not in out
    assert "every terminal ACS shipment agrees" not in out

    # Both.
    _run_replay(orders=[shipped.id, stranded.id])
    out = capsys.readouterr().out
    assert "1 voucher(s) retired on an order that already shipped" in out
    assert "1 order(s) disagree with a terminal shipment state" in out
    assert "every terminal ACS shipment agrees" not in out
    assert "no order disagrees" not in out


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
