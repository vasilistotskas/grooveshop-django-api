"""Unit tests for the Phase 2 ACS Smartpoint feature gates."""

from __future__ import annotations

import pytest

from shipping.enum import ShippingKind
from shipping.interfaces import get_provider

pytestmark = pytest.mark.django_db


@pytest.fixture
def acs_setting_off():
    """Force ACS_SMARTPOINT_ENABLED=False for the test."""
    from extra_settings.models import Setting

    setting, _ = Setting.objects.get_or_create(
        name="ACS_SMARTPOINT_ENABLED",
        defaults={"value_type": "bool", "value_bool": False},
    )
    setting.value_bool = False
    setting.save(update_fields=["value_bool"])
    yield


@pytest.fixture
def acs_setting_on():
    from extra_settings.models import Setting

    setting, _ = Setting.objects.get_or_create(
        name="ACS_SMARTPOINT_ENABLED",
        defaults={"value_type": "bool", "value_bool": True},
    )
    setting.value_bool = True
    setting.save(update_fields=["value_bool"])
    yield


def test_pickup_point_disabled_when_setting_off(acs_setting_off):
    adapter = get_provider("acs")
    assert adapter.is_kind_enabled(ShippingKind.PICKUP_POINT) is False


def test_home_delivery_always_enabled():
    adapter = get_provider("acs")
    assert adapter.is_kind_enabled(ShippingKind.HOME_DELIVERY) is True


def test_pickup_point_enabled_when_setting_on(acs_setting_on):
    adapter = get_provider("acs")
    assert adapter.is_kind_enabled(ShippingKind.PICKUP_POINT) is True


def test_validate_payload_blocks_when_locker_id_missing():
    adapter = get_provider("acs")
    errors = adapter.validate_order_payload(
        kind=ShippingKind.PICKUP_POINT,
        payload={},
    )
    assert "acs_station_external_id" in errors


def test_validate_payload_passes_with_locker_id():
    adapter = get_provider("acs")
    errors = adapter.validate_order_payload(
        kind=ShippingKind.PICKUP_POINT,
        payload={"acs_station_external_id": "LOC-001"},
    )
    assert errors == {}
