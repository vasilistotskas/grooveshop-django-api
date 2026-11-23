"""A locker the partner stops returning is withdrawn after a run of absences,
never at the first: the partner list churns about 3% a day. A list that is
empty or far smaller than the active lockers moves nothing."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from shipping_boxnow.factories import BoxNowLockerFactory
from shipping_boxnow.models import BoxNowLocker
from shipping_boxnow.services import BoxNowService

pytestmark = pytest.mark.django_db

IDS = [str(n) for n in range(10)]


def _destination(external_id: str) -> dict:
    return {
        "id": external_id,
        "locationType": "apm",
        "name": f"Locker {external_id}",
        "title": external_id,
        "lat": "37.9750",
        "lng": "23.7350",
        "country": "GR",
    }


def _sync(ids):
    client = MagicMock()
    client.return_value.list_destinations.return_value = [
        _destination(i) for i in ids
    ]
    client.return_value.partner_id = "1"
    with patch("shipping_boxnow.services.BoxNowClient", client):
        return BoxNowService.sync_lockers()


@pytest.fixture
def lockers(settings):
    settings.BOXNOW_LOCKER_ABSENT_SYNCS_BEFORE_DEACTIVATION = 3
    return [BoxNowLockerFactory(external_id=i) for i in IDS]


def _state(external_id):
    locker = BoxNowLocker.objects.get(external_id=external_id)
    return locker.is_active, locker.consecutive_absent_syncs


def test_one_absence_keeps_the_locker_and_a_return_resets_the_count(lockers):
    _sync(IDS[1:])
    assert _state("0") == (True, 1)

    _sync(IDS)
    assert _state("0") == (True, 0)


def test_the_nth_consecutive_absence_deactivates_and_the_one_before_does_not(
    lockers,
):
    _sync(IDS[1:])
    _sync(IDS[1:])
    assert _state("0") == (True, 2)

    stats = _sync(IDS[1:])

    assert _state("0") == (False, 3)
    assert stats["deactivated"] == 1


def test_a_deactivated_locker_the_partner_returns_again_is_reactivated(
    lockers,
):
    for _ in range(3):
        _sync(IDS[1:])

    _sync(IDS)

    assert _state("0") == (True, 0)


def test_an_empty_list_changes_nothing_and_logs_an_error(lockers, caplog):
    with caplog.at_level(logging.ERROR, logger="shipping_boxnow.services"):
        stats = _sync([])

    assert stats["deactivated"] == 0
    assert {_state(i) for i in IDS} == {(True, 0)}
    assert [r for r in caplog.records if r.levelno == logging.ERROR]


def test_a_list_below_the_minimum_fraction_changes_nothing(
    lockers, settings, caplog
):
    settings.BOXNOW_LOCKER_SYNC_MIN_FRACTION = 0.5

    with caplog.at_level(logging.ERROR, logger="shipping_boxnow.services"):
        _sync(IDS[:4])

    assert {_state(i) for i in IDS[4:]} == {(True, 0)}
    assert [r for r in caplog.records if r.levelno == logging.ERROR]


def test_lockers_the_response_adds_do_not_raise_the_bar_of_the_guard(
    lockers, settings
):
    """Ten active lockers, a response of six other ones: six is more than
    half of the ten that were active, so the absences are counted."""
    settings.BOXNOW_LOCKER_SYNC_MIN_FRACTION = 0.5

    _sync([f"new-{n}" for n in range(6)])

    assert _state("0") == (True, 1)
