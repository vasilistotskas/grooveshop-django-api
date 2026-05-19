"""Unit tests for the Phase 2 ACS Smartpoint feature gates."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from extra_settings.models import Setting

from shipping.enum import ShippingKind
from shipping.interfaces import get_provider

pytestmark = pytest.mark.django_db


def _patch_setting_get(value: bool):
    """Patch ``Setting.get`` so the carrier reads our chosen value for
    ``ACS_SMARTPOINT_ENABLED`` without touching the ``Setting`` table.

    Earlier ``Setting.objects.get_or_create`` + ``.save()`` flaked under
    CI's parallel xdist run: the autouse ``_reseed_extra_settings``
    fixture (conftest.py) rewrites the same ``EXTRA_SETTINGS_DEFAULTS``
    rows for every test on every worker, and the resulting savepoint-
    visibility interaction occasionally caused ``Setting.get`` to
    return the seeded default of ``False`` instead of the just-written
    ``True``. Patching the read site bypasses the round-trip entirely.
    """
    real_get = Setting.get.__func__

    def stub(cls, key, default=None):
        if key == "ACS_SMARTPOINT_ENABLED":
            return value
        return real_get(cls, key, default)

    return patch.object(Setting, "get", classmethod(stub))


@pytest.fixture
def acs_setting_off():
    with _patch_setting_get(False):
        yield


@pytest.fixture
def acs_setting_on():
    with _patch_setting_get(True):
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
