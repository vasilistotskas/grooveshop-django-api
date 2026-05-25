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


def test_status5_with_non_delivery_reason_maps_to_attempted():
    """ACS uses ``shipment_status=5`` for both successful delivery (paired
    with ``delivery_flag=1``) and failed delivery attempts (paired with
    ``non_delivery_reason_code``). When the reason code is populated the
    state is ATTEMPTED."""
    payload = {
        "delivery_flag": 0,
        "returned_flag": 0,
        "shipment_status": 5,
        "non_delivery_reason_code": "1",  # e.g. "recipient absent"
    }
    assert (
        AcsShipmentState.from_tracking_summary(
            payload, current=AcsShipmentState.OUT_FOR_DELIVERY
        )
        == AcsShipmentState.ATTEMPTED
    )


def test_status5_without_non_delivery_reason_preserves_current_state():
    """Live ACS tracking emits ``shipment_status=5`` for parcels that
    have only just departed the origin warehouse — ``delivery_flag=0``,
    ``returned_flag=0``, ``non_delivery_reason_code=""``,
    ``delivery_info="Η αποστολή βρίσκεται στην διαδρομή προς το
    κατάστημα παράδοσης"``. Mapping that to ATTEMPTED was painting
    every active shipment with a wrong "Delivery attempted" badge in
    admin (reported 2026-05-18). Without a reason code we hold the
    current state and let a subsequent poll lift it via a clearer
    signal (delivery_flag=1, shipment_status=4, etc.)."""
    payload = {
        "delivery_flag": 0,
        "returned_flag": 0,
        "shipment_status": 5,
        "non_delivery_reason_code": "",
    }
    assert (
        AcsShipmentState.from_tracking_summary(
            payload, current=AcsShipmentState.NEW
        )
        == AcsShipmentState.NEW
    )


def test_status5_with_missing_non_delivery_reason_preserves_current_state():
    """The reason-code field is sometimes absent from the summary (not
    just empty). Treat the same as empty — preserve current state."""
    payload = {
        "delivery_flag": 0,
        "returned_flag": 0,
        "shipment_status": 5,
        # non_delivery_reason_code intentionally omitted
    }
    assert (
        AcsShipmentState.from_tracking_summary(
            payload, current=AcsShipmentState.IN_TRANSIT
        )
        == AcsShipmentState.IN_TRANSIT
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
