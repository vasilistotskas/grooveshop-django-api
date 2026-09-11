"""The locker sync has to say WHOSE catalogue it just wrote.

A BoxNow locker id only means something to the partner account that
issued it. Staging failed every voucher with "invalid locker" for days
because its table held production's lockers while its credentials
pointed at a different partner, and no log line connected the two. The
sync now names the partner on every run and shouts when a run replaces
most of the catalogue, which is what a credentials mismatch looks like
from the inside.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from shipping_boxnow.factories import BoxNowLockerFactory
from shipping_boxnow.services import BoxNowService

pytestmark = pytest.mark.django_db


def _destination(external_id: str) -> dict:
    return {
        "id": external_id,
        "locationType": "apm",
        "name": f"Locker {external_id}",
        "title": external_id,
        "lat": "37.9750",
        "lng": "23.7350",
    }


def _run_sync(destinations, partner_id="10391"):
    client = MagicMock()
    client.return_value.list_destinations.return_value = destinations
    client.return_value.partner_id = partner_id
    with patch("shipping_boxnow.services.BoxNowClient", client):
        return BoxNowService.sync_lockers()


def test_the_run_names_the_partner_it_fetched_for(caplog):
    with caplog.at_level(logging.INFO, logger="shipping_boxnow.services"):
        _run_sync([_destination("a")], partner_id="15135")

    assert "partner=15135" in caplog.text


def test_the_run_reports_how_many_destinations_came_back(caplog):
    with caplog.at_level(logging.INFO, logger="shipping_boxnow.services"):
        _run_sync([_destination("a"), _destination("b")])

    assert "returned 2 destinations" in caplog.text


def test_replacing_most_of_the_catalogue_warns_about_the_account(caplog):
    """Four known lockers, a catalogue that shares none of them."""
    for external_id in ("1", "2", "3", "4"):
        BoxNowLockerFactory(external_id=external_id, is_active=True)

    with caplog.at_level(logging.WARNING, logger="shipping_boxnow.services"):
        _run_sync([_destination("9001")], partner_id="10391")

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "a wholesale catalogue swap must warn"
    message = warnings[0].getMessage()
    assert "different BoxNow account" in message
    assert "10391" in message


def test_a_normal_refresh_does_not_warn(caplog):
    for external_id in ("1", "2", "3", "4"):
        BoxNowLockerFactory(external_id=external_id, is_active=True)

    with caplog.at_level(logging.WARNING, logger="shipping_boxnow.services"):
        _run_sync([_destination(i) for i in ("1", "2", "3", "4", "5")])

    assert not [r for r in caplog.records if r.levelno == logging.WARNING]
