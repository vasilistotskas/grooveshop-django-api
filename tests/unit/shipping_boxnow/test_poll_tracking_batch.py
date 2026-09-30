"""Single-flight lock and candidate selection of ``poll_boxnow_tracking_batch``.

Covers ``shipping_boxnow/tasks.py``: the batch dispatcher skips without
touching another worker's lock, releases its own lock after success and
after a failed dispatch, skips unconfigured tenants before locking, and
fans out only non-terminal, stale shipments with a parcel id, oldest poll
first, staggered and capped. Mirrors
``tests/unit/shipping_acs/test_poll_tracking_batch.py``; the broker
(``apply_async``) is the only thing mocked.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import call, patch
from uuid import uuid4

import pytest
from django.core.cache.backends.locmem import LocMemCache
from django.utils import timezone
from kombu.exceptions import OperationalError

from shipping_boxnow.enum.parcel_state import BoxNowParcelState
from shipping_boxnow.factories import BoxNowShipmentFactory
from shipping_boxnow.tasks import poll_boxnow_tracking_batch

pytestmark = pytest.mark.django_db

_LOCK_KEY = "boxnow:poll_batch:lock"


@pytest.fixture
def cache():
    """A private cache so the lock is not shared with other xdist workers."""
    local = LocMemCache(f"boxnow-poll-batch-{uuid4()}", {})
    with patch("django.core.cache.cache", local):
        yield local


@pytest.fixture
def dispatch():
    with patch(
        "shipping_boxnow.tasks.poll_boxnow_tracking_one.apply_async"
    ) as mocked:
        yield mocked


def _stale(minutes: int = 60, **kwargs):
    return BoxNowShipmentFactory(
        with_parcel=True,
        last_polled_at=timezone.now() - timedelta(minutes=minutes),
        **kwargs,
    )


def test_lock_held_skips_and_leaves_the_other_workers_lock(
    boxnow_configured_tenant, cache, dispatch
):
    _stale()
    cache.set(_LOCK_KEY, "other-worker", 600)

    result = poll_boxnow_tracking_batch.run()

    assert result == {"dispatched": 0, "skipped": True}
    dispatch.assert_not_called()
    assert cache.get(_LOCK_KEY) == "other-worker"


def test_lock_is_released_after_a_successful_run(
    boxnow_configured_tenant, cache, dispatch
):
    shipment = _stale()

    result = poll_boxnow_tracking_batch.run()

    assert result == {"dispatched": 1}
    dispatch.assert_called_once_with(args=[shipment.id], countdown=0.0)
    assert cache.get(_LOCK_KEY) is None


def test_lock_is_released_when_dispatch_fails(
    boxnow_configured_tenant, cache, dispatch
):
    _stale()
    dispatch.side_effect = OperationalError("broker down")

    with pytest.raises(OperationalError):
        poll_boxnow_tracking_batch.run()

    assert cache.get(_LOCK_KEY) is None


def test_unconfigured_tenant_skips_without_taking_the_lock(
    bind_tenant, cache, dispatch
):
    bind_tenant(SimpleNamespace(schema_name="no-boxnow"))
    _stale()

    with patch.object(cache, "add", wraps=cache.add) as add:
        result = poll_boxnow_tracking_batch.run()

    assert result == {"dispatched": 0, "skipped": True}
    add.assert_not_called()
    dispatch.assert_not_called()


def test_only_stale_non_terminal_parcels_are_polled_oldest_first(
    boxnow_configured_tenant, cache, dispatch
):
    oldest = _stale(minutes=300, parcel_state=BoxNowParcelState.IN_DEPOT)
    older = _stale(minutes=120)
    never_polled = BoxNowShipmentFactory(with_parcel=True, last_polled_at=None)
    _stale(minutes=5)  # polled too recently
    BoxNowShipmentFactory(
        parcel_id=None, last_polled_at=None
    )  # never created at BoxNow
    for terminal in (
        BoxNowParcelState.PENDING_CREATION,
        BoxNowParcelState.DELIVERED,
        BoxNowParcelState.RETURNED,
        BoxNowParcelState.EXPIRED,
        BoxNowParcelState.CANCELED,
        BoxNowParcelState.LOST,
        BoxNowParcelState.MISSING,
    ):
        _stale(parcel_state=terminal)

    result = poll_boxnow_tracking_batch.run()

    assert result == {"dispatched": 3}
    # Postgres sorts NULL last on an ascending order_by.
    assert dispatch.call_args_list == [
        call(args=[oldest.id], countdown=0.0),
        call(args=[older.id], countdown=0.2),
        call(args=[never_polled.id], countdown=0.4),
    ]


def test_fan_out_is_capped_at_max_per_run(
    boxnow_configured_tenant, cache, dispatch
):
    first = _stale(minutes=90)
    _stale(minutes=60)

    result = poll_boxnow_tracking_batch.run(max_per_run=1)

    assert result == {"dispatched": 1}
    dispatch.assert_called_once_with(args=[first.id], countdown=0.0)
