"""Unit tests for the ACS carrier's per-kind gates.

Locker pickup is offered only while active locker ``AcsStation`` rows
exist for the delivery country; there is no setting to flip.
"""

from __future__ import annotations

import pytest

from shipping.enum import ShippingKind
from shipping.interfaces import get_provider
from shipping_acs.enum.shop_kind import AcsShopKind
from shipping_acs.factories import AcsStationFactory

pytestmark = pytest.mark.django_db

PICKUP = ShippingKind.PICKUP_POINT
HOME = ShippingKind.HOME_DELIVERY


def test_pickup_point_disabled_before_stations_are_synced(
    acs_configured_tenant,
):
    adapter = get_provider("acs")
    assert adapter.is_kind_enabled(PICKUP, country_code="GR") is False
    assert adapter.is_kind_enabled(PICKUP, country_code=None) is False


def test_home_delivery_needs_no_stations(acs_configured_tenant):
    adapter = get_provider("acs")
    assert adapter.is_kind_enabled(HOME, country_code="GR") is True
    assert adapter.is_kind_enabled(HOME, country_code=None) is True


def test_pickup_point_enabled_with_a_locker_in_the_country(
    acs_configured_tenant,
):
    AcsStationFactory(country_code="GR")
    adapter = get_provider("acs")
    assert adapter.is_kind_enabled(PICKUP, country_code="GR") is True
    assert adapter.is_kind_enabled(PICKUP, country_code="gr") is True


def test_pickup_point_is_per_country(acs_configured_tenant):
    AcsStationFactory(country_code="GR")
    adapter = get_provider("acs")
    assert adapter.is_kind_enabled(PICKUP, country_code="CY") is False


def test_without_a_country_any_country_with_a_locker_counts(
    acs_configured_tenant,
):
    AcsStationFactory(country_code="GR")
    adapter = get_provider("acs")
    assert adapter.is_kind_enabled(PICKUP, country_code=None) is True


def test_deactivated_stations_do_not_count(acs_configured_tenant):
    AcsStationFactory(country_code="GR", is_active=False)
    adapter = get_provider("acs")
    assert adapter.is_kind_enabled(PICKUP, country_code="GR") is False
    assert adapter.is_kind_enabled(PICKUP, country_code=None) is False


def test_shops_are_not_lockers(acs_configured_tenant):
    """The picker lists locker kinds only; a plain ACS shop must not
    switch the locker option on."""
    AcsStationFactory(country_code="GR", shop_kind=AcsShopKind.SHOP)
    adapter = get_provider("acs")
    assert adapter.is_kind_enabled(PICKUP, country_code="GR") is False
    assert adapter.is_kind_enabled(PICKUP, country_code=None) is False


def test_pickup_point_disabled_without_acs_credentials():
    """Stations alone do not make ACS available: an unconfigured tenant
    (no Tenant.acs_* credentials) sees ACS entirely unavailable."""
    AcsStationFactory(country_code="GR")
    adapter = get_provider("acs")
    assert adapter.is_kind_enabled(PICKUP, country_code="GR") is False


def test_home_delivery_disabled_without_acs_credentials():
    """Home delivery also requires tenant ACS credentials, not just
    ``ShippingProvider.is_active``."""
    adapter = get_provider("acs")
    assert adapter.is_kind_enabled(HOME, country_code="GR") is False


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
