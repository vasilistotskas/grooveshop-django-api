"""Unit tests for AcsShipmentState.from_tracking_summary mapping."""

from __future__ import annotations

import pytest

from shipping_acs.enum.shipment_state import AcsShipmentState


@pytest.mark.parametrize(
    "payload, current, expected",
    [
        (
            {"delivery_flag": 1, "returned_flag": 0, "shipment_status": 5},
            AcsShipmentState.NEW,
            AcsShipmentState.DELIVERED,
        ),
        (
            {"delivery_flag": 0, "returned_flag": 1, "shipment_status": 5},
            AcsShipmentState.IN_TRANSIT,
            AcsShipmentState.RETURNED,
        ),
        (
            {
                "delivery_flag": "0",
                "returned_flag": "0",
                "shipment_status": "1",
            },
            AcsShipmentState.PENDING_CREATION,
            AcsShipmentState.NEW,
        ),
        (
            {"delivery_flag": 0, "returned_flag": 0, "shipment_status": 4},
            AcsShipmentState.IN_TRANSIT,
            AcsShipmentState.OUT_FOR_DELIVERY,
        ),
        (
            {"delivery_flag": 0, "returned_flag": 0, "shipment_status": 2},
            AcsShipmentState.NEW,
            AcsShipmentState.IN_TRANSIT,
        ),
    ],
)
def test_from_tracking_summary(payload, current, expected):
    assert (
        AcsShipmentState.from_tracking_summary(payload, current=current)
        == expected
    )


def test_from_tracking_summary_falls_back_to_current_for_garbage():
    payload = {"delivery_flag": "wat", "shipment_status": "bork"}
    assert (
        AcsShipmentState.from_tracking_summary(
            payload, current=AcsShipmentState.AT_DESTINATION
        )
        == AcsShipmentState.AT_DESTINATION
    )


def test_from_tracking_summary_returned_takes_precedence_over_delivery():
    # An anomalous payload (both flags set) — returned_flag wins because
    # returned packages are not "delivered" to the customer.
    payload = {"delivery_flag": 1, "returned_flag": 1, "shipment_status": 5}
    assert (
        AcsShipmentState.from_tracking_summary(
            payload, current=AcsShipmentState.OUT_FOR_DELIVERY
        )
        == AcsShipmentState.RETURNED
    )
