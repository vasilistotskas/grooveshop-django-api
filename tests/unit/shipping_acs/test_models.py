"""Unit tests for AcsShipment / AcsTrackingEvent / AcsStation models."""

from __future__ import annotations

import pytest

from order.factories.order import OrderFactory
from shipping_acs.enum.shipment_state import AcsShipmentState
from shipping_acs.factories import (
    AcsShipmentFactory,
    AcsStationFactory,
    AcsTrackingEventFactory,
)
from shipping_acs.models import AcsShipment

pytestmark = pytest.mark.django_db


def test_voucher_no_unique_allows_multiple_pending_creation_rows():
    """Multiple PENDING_CREATION rows must coexist before vouchers are minted.

    Verifies the null=True UNIQUE constraint pattern (Postgres treats
    NULL as distinct), matching BoxNow's idempotency model.
    """
    order_a = OrderFactory()
    order_b = OrderFactory()
    AcsShipmentFactory(order=order_a)
    AcsShipmentFactory(order=order_b)

    pending = AcsShipment.objects.filter(voucher_no__isnull=True)
    assert pending.count() == 2


def test_voucher_no_unique_rejects_duplicate_assigned_numbers():
    order_a = OrderFactory()
    AcsShipmentFactory(order=order_a, voucher_no="7227891234")

    order_b = OrderFactory()
    with pytest.raises(Exception):
        AcsShipmentFactory(order=order_b, voucher_no="7227891234")


def test_is_active_true_for_new_state():
    shipment = AcsShipmentFactory(
        shipment_state=AcsShipmentState.NEW, voucher_no="9999999999"
    )
    assert shipment.is_active is True


def test_is_active_false_for_terminal_state():
    shipment = AcsShipmentFactory(
        shipment_state=AcsShipmentState.DELIVERED,
        voucher_no="9999999998",
    )
    assert shipment.is_active is False


def test_tracking_event_fingerprint_unique():
    event_a = AcsTrackingEventFactory(event_fingerprint="abc123")
    with pytest.raises(Exception):
        AcsTrackingEventFactory(
            shipment=event_a.shipment, event_fingerprint="abc123"
        )


def test_station_string_representation():
    station = AcsStationFactory(external_id="SDK", name="ΘΕΣΣΑΛΟΝΙΚΗ")
    assert "SDK" in str(station)
    assert "ΘΕΣΣΑΛΟΝΙΚΗ" in str(station)
