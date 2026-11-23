"""BOX NOW's public locker catalogue: strict shape, sizes by exact
membership, and what is kept when it cannot be read."""

from __future__ import annotations

import json
import logging
from unittest.mock import patch

import pytest
from django.conf import settings

from core.net.safe_fetch import FetchedResponse, FetchTemporaryError
from shipping_boxnow.catalogue import (
    CatalogueError,
    apply_catalogues,
    fetch_catalogues,
    parse_catalogue,
)
from shipping_boxnow.factories import BoxNowLockerFactory
from shipping_boxnow.models import BoxNowLocker

pytestmark = pytest.mark.django_db


def _row(locker_id, sizes=("3", "2", "1"), probability=1, **extra):
    return {
        "address": "Ermou 1, Athens",
        "id": locker_id,
        "lat": "37.97",
        "lng": "23.73",
        "postalCode": "10563",
        "probability": probability,
        "sizes": list(sizes),
        "title": f"Locker {locker_id}",
        **extra,
    }


def _body(rows):
    return json.dumps({row["id"]: row for row in rows}).encode()


def _serve(body):
    return patch(
        "shipping_boxnow.catalogue.safe_fetch",
        return_value=FetchedResponse(
            url="https://x/all.json", content=body, content_type="json"
        ),
    )


def sync_catalogue_facts():
    """One fetch for every store, then this store's lockers."""
    return apply_catalogues(fetch_catalogues())


def test_todays_shape_is_accepted():
    facts = parse_catalogue(_body([_row("1", ("3", "1"), 0.5)]))

    assert facts["1"].sizes == [1, 3]
    assert facts["1"].probability == 0.5


@pytest.mark.parametrize(
    "row",
    [
        {k: v for k, v in _row("1").items() if k != "title"},
        _row("1", sizes=(3, 2, 1)),
        _row("1", sizes=("4",)),
        _row("1", probability=1.5),
        _row("1", probability=-0.1),
        _row("1", surprise="x"),
    ],
)
def test_a_changed_shape_is_refused(row):
    with pytest.raises(CatalogueError):
        parse_catalogue(_body([row]))


def test_a_key_that_differs_from_the_id_is_refused():
    body = json.dumps({"1": _row("2")}).encode()

    with pytest.raises(CatalogueError):
        parse_catalogue(body)


@pytest.mark.usefixtures("boxnow_live_network")
def test_sizes_are_set_by_exact_membership():
    BoxNowLockerFactory(external_id="1", compartment_sizes=[])
    BoxNowLockerFactory(external_id="2", compartment_sizes=[])
    body = _body([_row("1", ("3", "1")), _row("2", ("3", "2", "1"), 0.4)])

    with _serve(body):
        sync_catalogue_facts()

    one = BoxNowLocker.objects.get(external_id="1")
    two = BoxNowLocker.objects.get(external_id="2")
    assert one.compartment_sizes == [1, 3]
    assert 2 not in one.compartment_sizes
    assert two.availability_probability == 0.4


@pytest.mark.usefixtures("boxnow_live_network")
def test_a_locker_missing_from_the_catalogue_is_unknown(caplog):
    BoxNowLockerFactory(external_id="1")
    BoxNowLockerFactory(external_id="gone")

    with caplog.at_level(logging.WARNING, logger="shipping_boxnow.catalogue"):
        with _serve(_body([_row("1")])):
            result = sync_catalogue_facts()

    gone = BoxNowLocker.objects.get(external_id="gone")
    assert gone.compartment_sizes == []
    assert gone.availability_probability is None
    assert result["unknown"] == 1
    assert "gone" in caplog.text


@pytest.mark.usefixtures("boxnow_live_network")
def test_a_changed_catalogue_keeps_the_stored_sizes_and_logs_an_error(caplog):
    BoxNowLockerFactory(external_id="1", compartment_sizes=[2])

    with caplog.at_level(logging.ERROR, logger="shipping_boxnow.catalogue"):
        with _serve(_body([_row("1", sizes=(3,), surprise="x")])):
            result = sync_catalogue_facts()

    assert BoxNowLocker.objects.get(external_id="1").compartment_sizes == [2]
    assert result == {"updated": 0, "unknown": 0}
    assert "GR" in caplog.text


@pytest.mark.usefixtures("boxnow_live_network")
def test_a_failed_fetch_keeps_the_stored_sizes(caplog):
    BoxNowLockerFactory(external_id="1", compartment_sizes=[2])

    with caplog.at_level(logging.ERROR, logger="shipping_boxnow.catalogue"):
        with patch(
            "shipping_boxnow.catalogue.safe_fetch",
            side_effect=FetchTemporaryError("down"),
        ):
            sync_catalogue_facts()

    assert BoxNowLocker.objects.get(external_id="1").compartment_sizes == [2]


def test_the_catalogue_is_fetched_once_per_country_for_every_store():
    with _serve(_body([_row("1")])) as fetch:
        catalogues = fetch_catalogues()

    assert fetch.call_count == len(settings.BOXNOW_LOCKER_CATALOGUE_COUNTRIES)
    assert catalogues["GR"] == {"1": [[1, 2, 3], 1.0]}


def test_a_catalogue_row_the_partner_does_not_hold_is_ignored():
    BoxNowLockerFactory(external_id="1", compartment_sizes=[])

    with _serve(_body([_row("1"), _row("999")])):
        sync_catalogue_facts()

    assert not BoxNowLocker.objects.filter(external_id="999").exists()


def test_the_beat_task_fetches_once_and_hands_the_copy_to_every_store():
    from shipping_boxnow import tasks

    catalogue = {"GR": {"1": [[1], 1.0]}}
    with (
        patch(
            "shipping_boxnow.catalogue.fetch_catalogues",
            return_value=catalogue,
        ) as fetch,
        patch("tenant.celery.run_for_all_tenants") as fan_out,
    ):
        tasks.sync_boxnow_locker_catalogue()

    fetch.assert_called_once_with()
    fan_out.assert_called_once_with(
        "shipping_boxnow.tasks.apply_boxnow_locker_catalogue",
        catalogue=catalogue,
    )


def test_the_beat_task_dispatches_nothing_when_no_catalogue_was_read():
    from shipping_boxnow import tasks

    with (
        patch("shipping_boxnow.catalogue.fetch_catalogues", return_value={}),
        patch("tenant.celery.run_for_all_tenants") as fan_out,
    ):
        assert tasks.sync_boxnow_locker_catalogue() == []

    fan_out.assert_not_called()


def test_a_store_without_boxnow_skips_the_application():
    from shipping_boxnow import tasks

    with patch(
        "shipping_boxnow.tasks._skip_if_boxnow_unconfigured",
        return_value=True,
    ):
        assert tasks.apply_boxnow_locker_catalogue({"GR": {}}) == {
            "updated": 0,
            "unknown": 0,
        }


@pytest.mark.usefixtures("boxnow_live_network")
@pytest.mark.parametrize("rows", [[], ["1"]])
def test_an_empty_or_truncated_catalogue_keeps_the_sizes(rows, caplog):
    for external_id in ("1", "2", "3", "4"):
        BoxNowLockerFactory(external_id=external_id, compartment_sizes=[2])

    with caplog.at_level(logging.ERROR, logger="shipping_boxnow.catalogue"):
        with _serve(_body([_row(i) for i in rows])):
            sync_catalogue_facts()

    sizes = {
        tuple(locker.compartment_sizes) for locker in BoxNowLocker.objects.all()
    }
    assert sizes == {(2,)}
    assert "minimum fraction" in caplog.text


@pytest.mark.usefixtures("boxnow_live_network")
def test_an_unchanged_catalogue_writes_nothing_and_purges_nothing():
    BoxNowLockerFactory(
        external_id="1",
        compartment_sizes=[1, 2, 3],
        availability_probability=1.0,
    )

    with (
        _serve(_body([_row("1")])),
        patch("shipping_boxnow.catalogue.mark_surface_stale") as purge,
    ):
        sync_catalogue_facts()

    purge.assert_not_called()


def test_a_stage_store_takes_no_size_from_a_colliding_id(
    boxnow_configured_tenant,
):
    """Stage locker 5 is not production locker 5: the catalogue's row 5
    sizes nothing on a stage store, and what an earlier sync set there is
    reset to unknown."""
    BoxNowLockerFactory(
        external_id="5",
        compartment_sizes=[1, 2],
        availability_probability=0.9,
    )
    BoxNowLockerFactory(external_id="6", compartment_sizes=[])

    with (
        _serve(_body([_row("5", ("3",), 0.4), _row("6")])),
        patch("shipping_boxnow.catalogue.mark_surface_stale") as purge,
    ):
        result = sync_catalogue_facts()

    for locker in BoxNowLocker.objects.all():
        assert locker.compartment_sizes == []
        assert locker.availability_probability is None
    assert result == {"updated": 0, "unknown": 0}
    purge.assert_called_once_with("shipping")


def test_a_stage_store_with_nothing_sized_writes_nothing(
    boxnow_configured_tenant,
):
    BoxNowLockerFactory(
        external_id="5", compartment_sizes=[], availability_probability=None
    )

    with (
        _serve(_body([_row("5")])),
        patch("shipping_boxnow.catalogue.mark_surface_stale") as purge,
    ):
        sync_catalogue_facts()

    assert BoxNowLocker.objects.get(external_id="5").compartment_sizes == []
    purge.assert_not_called()
