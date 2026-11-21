"""A catalogue sync purges the cached pickup-point lists.

``GET /shipping/boxnow/lockers`` and ``GET /shipping/acs/stations`` are
cached (``cache_methods``) and purged with the ``shipping`` surface. A
sync, the daily one and the order-time resync alike, must purge it, or an
exact lookup keeps answering with a catalogue the sync replaced: a new
point missing, a withdrawn one still listed.
"""

from __future__ import annotations

from unittest import mock

import pytest

from core.cache.invalidation import (
    _pending_codes,
    connect_surface_invalidation,
    disconnect_surface_invalidation,
)
from shipping_acs.enum.shop_kind import AcsShopKind
from shipping_acs.factories import AcsStationFactory
from shipping_acs.services import AcsService
from shipping_boxnow.factories import BoxNowLockerFactory
from shipping_boxnow.models import BoxNowLocker
from shipping_boxnow.services import BoxNowService

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _connected(settings):
    settings.DISABLE_CACHE = False
    _pending_codes().clear()
    connect_surface_invalidation(force=True)
    yield
    disconnect_surface_invalidation()
    _pending_codes().clear()


@pytest.fixture
def purged():
    """The surface codes handed to ``CacheService.purge``."""
    with mock.patch("core.cache.service.CacheService.purge") as purge:
        purge.return_value = mock.Mock(
            failed_surfaces=[], django_headline=0, nuxt_headline=0
        )
        yield (
            lambda: {
                code for call in purge.call_args_list for code in call.args[0]
            },
            purge.reset_mock,
        )


def _destination(external_id: str, country: str = "GR") -> dict:
    return {
        "id": external_id,
        "locationType": "apm",
        "name": f"Locker {external_id}",
        "title": external_id,
        "lat": "37.9750",
        "lng": "23.7350",
        "addressLine1": "Ermou 40",
        "addressLine2": "Athens",
        "postalCode": "105 63",
        "country": country,
    }


def _sync_lockers(destinations: list[dict]) -> dict:
    client = mock.MagicMock()
    client.return_value.partner_id = "1"
    client.return_value.list_destinations.return_value = destinations
    with mock.patch("shipping_boxnow.services.BoxNowClient", client):
        return BoxNowService.sync_lockers()


class TestTheLockerSync:
    def test_a_locker_it_adds_purges_the_list(self, purged):
        codes, reset = purged
        reset()

        _sync_lockers([_destination("new-1")])

        assert BoxNowLocker.objects.filter(external_id="new-1").exists()
        assert "shipping" in codes()

    def test_a_locker_it_only_withdraws_purges_the_list(self, purged):
        """A destination without a country is kept as seen but not
        written, so this sync's one write is the deactivating
        ``update``, which sends no signal."""
        codes, reset = purged
        BoxNowLockerFactory(external_id="old-1", consecutive_absent_syncs=2)
        reset()

        result = _sync_lockers([_destination("kept-1", country="")])

        assert result["deactivated"] == 1
        assert "shipping" in codes()


class TestTheStationSync:
    def test_a_station_it_withdraws_marks_the_list_stale(
        self, monkeypatch, purged
    ):
        """The deactivating ``update`` marks the surface itself rather
        than leaning on the rows the same sync upserts."""
        from shipping_acs import services

        AcsStationFactory(
            external_id="GONE",
            country_code="GR",
            shop_kind=AcsShopKind.SMARTPOINT_LOCKER,
        )
        row = {
            "ACS_SHOP_STATION_ID_EN": "KEPT",
            "ACS_SHOP_BRANCH_ID": 1,
            "ACS_SHOP_LAT": "37.98",
            "ACS_SHOP_LONG": "23.72",
        }

        class _Client:
            def stations(self, **_kw):
                return [row]

        monkeypatch.setattr(services, "AcsClient", _Client)
        with mock.patch.object(services, "mark_surface_stale") as mark:
            result = AcsService.sync_stations(
                country="GR", kinds=(AcsShopKind.SMARTPOINT_LOCKER.value,)
            )

        assert result["deactivated"] == 1
        mark.assert_called_once_with("shipping")
