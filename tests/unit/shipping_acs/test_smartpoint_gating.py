"""Unit tests for the ACS carrier's per-kind gates.

Locker pickup is offered only while active locker ``AcsStation`` rows
exist for the delivery country; there is no setting to flip.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

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


def test_validate_payload_passes_with_a_deliverable_station():
    AcsStationFactory(external_id="LOC-001", branch_code="", country_code="GR")
    adapter = get_provider("acs")
    errors = adapter.validate_order_payload(
        kind=ShippingKind.PICKUP_POINT,
        payload={"acs_station_external_id": "LOC-001", "country_id": "GR"},
    )
    assert errors == {}


@pytest.mark.parametrize(
    ("country_code", "is_active"),
    [("CY", True), ("GR", False)],
    ids=["another-country", "withdrawn"],
)
def test_validate_payload_refuses_a_station_it_cannot_deliver_to(
    country_code, is_active
):
    AcsStationFactory(
        external_id="LOC-001",
        branch_code="",
        country_code=country_code,
        is_active=is_active,
    )
    adapter = get_provider("acs")
    errors = adapter.validate_order_payload(
        kind=ShippingKind.PICKUP_POINT,
        payload={"acs_station_external_id": "LOC-001", "country_id": "GR"},
    )
    assert set(errors) == {"acs_station_external_id"}


def test_a_set_of_countries_is_answered_in_one_station_query(
    acs_configured_tenant,
):
    AcsStationFactory(country_code="GR")
    AcsStationFactory(country_code="CY", shop_kind=AcsShopKind.SHOP)
    adapter = get_provider("acs")

    with CaptureQueriesContext(connection) as captured:
        enabled = adapter.kind_enabled_countries(PICKUP, ["gr", "CY", "BG"])

    assert enabled == {"GR"}
    station_queries = [
        q for q in captured if "shipping_acs_acsstation" in q["sql"]
    ]
    assert len(station_queries) == 1
    # The same answer as the one-country gate.
    assert enabled == {
        code
        for code in ("GR", "CY", "BG")
        if adapter.is_kind_enabled(PICKUP, country_code=code)
    }


def test_no_country_is_enabled_without_acs_credentials():
    AcsStationFactory(country_code="GR")

    assert not get_provider("acs").kind_enabled_countries(PICKUP, ["GR"])
    assert not get_provider("acs").kind_enabled_countries(HOME, ["GR"])
