"""Unit tests for AcsService.sync_stations.

Regression guards for the 2026-07-25 locker-collapse fix: the
(external_id, branch_code) PAIR is the locker identity — external_id
is the AREA station code shared by every locker in that area, so
upserting on external_id alone collapsed 1,485 GR lockers into ~135
rows and hid ~90% of Smartpoints from the checkout picker.
"""

from __future__ import annotations

import logging

import pytest

from shipping_acs.enum.shop_kind import AcsShopKind
from shipping_acs.factories import AcsStationFactory
from shipping_acs.models import AcsStation
from shipping_acs.services import AcsService

pytestmark = pytest.mark.django_db


def _locker_row(
    station_id: str,
    branch_id: int,
    *,
    name: str = "ACS SMARTPOINT LOCKER",
    address: str = "Test Street 1",
) -> dict:
    return {
        "ACS_SHOP_STATION_ID_EN": station_id,
        "ACS_SHOP_STATION_ID": station_id,
        "ACS_SHOP_BRANCH_ID": branch_id,
        "ACS_SHOP_STATION_DESCR": name,
        "ACS_SHOP_ADDRESS": address,
        "ACS_SHOP_AREA_DESCR": "ΑΘΗΝΑ",
        "ACS_SHOP_ZIPCODE": "11523",
        "ACS_SHOP_PHONES": "210-0000000",
        "ACS_SHOP_WORKING_HOURS": "00:00-23:59",
        "ACS_SHOP_LAT": "37.98",
        "ACS_SHOP_LONG": "23.72",
    }


@pytest.fixture
def stations_client(monkeypatch):
    """Patch AcsClient with a stations() stub fed per (country, kind)."""
    from shipping_acs import services

    class _StationsClient:
        responses: dict[int, list[dict]] = {}

        def stations(self, *, country="GR", shop_kind=None, language="GR"):
            return list(_StationsClient.responses.get(shop_kind, []))

    _StationsClient.responses = {}
    monkeypatch.setattr(services, "AcsClient", _StationsClient)
    return _StationsClient


class TestPairUpsert:
    def test_lockers_sharing_station_code_get_one_row_each(
        self, stations_client
    ):
        """Multiple lockers under one area station code (e.g. 50
        Athens lockers under 'ATH') must all be cached — one row per
        (external_id, branch_code) pair."""
        stations_client.responses = {
            8: [
                _locker_row("ATH", 521, address="Ιερά Οδός 8"),
                _locker_row("ATH", 567, address="Πειραιώς 100"),
                _locker_row("ATH", 513, address="Πέτρου Ράλλη 36"),
            ],
        }

        result = AcsService.sync_stations(country="GR", kinds=(8,))

        assert result["upserted"] == 3
        rows = AcsStation.objects.filter(external_id="ATH").order_by(
            "branch_code"
        )
        assert rows.count() == 3
        assert [r.branch_code for r in rows] == ["513", "521", "567"]

    def test_resync_updates_in_place_without_duplicates(self, stations_client):
        stations_client.responses = {
            8: [_locker_row("ATH", 521, address="Old Address")],
        }
        AcsService.sync_stations(country="GR", kinds=(8,))

        stations_client.responses = {
            8: [_locker_row("ATH", 521, address="New Address")],
        }
        AcsService.sync_stations(country="GR", kinds=(8,))

        rows = AcsStation.objects.filter(external_id="ATH")
        assert rows.count() == 1
        assert rows.get().address_line_1 == "New Address"

    def test_locker_absent_from_feed_is_deactivated(self, stations_client):
        """A cached locker missing from the API response is a removed
        locker — deactivate it, but only within kinds that returned
        data."""
        stations_client.responses = {
            8: [
                _locker_row("ATH", 521),
                _locker_row("ATH", 567),
            ],
        }
        AcsService.sync_stations(country="GR", kinds=(8,))

        stations_client.responses = {8: [_locker_row("ATH", 521)]}
        result = AcsService.sync_stations(country="GR", kinds=(8,))

        assert result["deactivated"] == 1
        assert not AcsStation.objects.get(
            external_id="ATH", branch_code="567"
        ).is_active
        assert AcsStation.objects.get(
            external_id="ATH", branch_code="521"
        ).is_active


class TestZeroRowsLogging:
    """The daily-noise fix: zero rows is only a WARNING when it
    contradicts cached active rows (transient upstream failure).
    A kind empty both upstream and locally (GR kind=7 'Smartpoint
    without locker' since 2026-07; CY kind=7 per the PDF) is a
    documented steady state → INFO."""

    def test_empty_kind_with_no_cache_logs_info_not_warning(
        self, stations_client, caplog
    ):
        stations_client.responses = {7: [], 8: [_locker_row("ATH", 521)]}

        with caplog.at_level(logging.INFO, logger="shipping_acs.services"):
            AcsService.sync_stations(country="GR", kinds=(7, 8))

        kind7_records = [
            r for r in caplog.records if "kind=7" in r.getMessage()
        ]
        assert kind7_records, "expected a log line for the empty kind"
        assert all(r.levelno == logging.INFO for r in kind7_records)

    def test_empty_kind_with_cached_rows_warns_and_keeps_cache(
        self, stations_client, caplog
    ):
        cached = AcsStationFactory(
            external_id="ATH",
            branch_code="521",
            shop_kind=AcsShopKind.SMARTPOINT_LOCKER,
            country_code="GR",
            is_active=True,
        )
        stations_client.responses = {8: []}

        with caplog.at_level(logging.INFO, logger="shipping_acs.services"):
            result = AcsService.sync_stations(country="GR", kinds=(8,))

        warning_records = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING and "kind=8" in r.getMessage()
        ]
        assert warning_records, "expected a WARNING for contradicted cache"
        # Cache survives the transient failure.
        cached.refresh_from_db()
        assert cached.is_active
        assert result["deactivated"] == 0
