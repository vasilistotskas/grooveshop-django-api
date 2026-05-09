"""Unit tests for AcsCarrier — the registry adapter."""

from __future__ import annotations

import pytest

from shipping.enum import ShippingKind
from shipping.interfaces import get_provider, is_registered


def test_acs_adapter_registers_under_code_acs():
    assert is_registered("acs") is True
    adapter = get_provider("acs")
    assert adapter.code == "acs"


@pytest.mark.django_db
def test_acs_validation_requires_locker_for_pickup_point():
    adapter = get_provider("acs")
    errors = adapter.validate_order_payload(
        kind=ShippingKind.PICKUP_POINT,
        payload={},
    )
    assert "acs_station_external_id" in errors


@pytest.mark.django_db
def test_acs_validation_passes_for_home_delivery():
    adapter = get_provider("acs")
    assert (
        adapter.validate_order_payload(
            kind=ShippingKind.HOME_DELIVERY, payload={}
        )
        == {}
    )


@pytest.mark.django_db
def test_calculate_shipping_cost_uses_settings_threshold():
    adapter = get_provider("acs")

    # Below threshold → returns flat rate (default 3.50 from settings).
    cheap = adapter.calculate_shipping_cost(
        order_value_amount=10.0,
        currency="EUR",
        kind=ShippingKind.HOME_DELIVERY,
    )
    assert cheap is not None
    assert cheap[0] > 0

    # At/above threshold (default 40) → free shipping.
    free = adapter.calculate_shipping_cost(
        order_value_amount=80.0,
        currency="EUR",
        kind=ShippingKind.HOME_DELIVERY,
    )
    assert free == (0.0, "EUR")
