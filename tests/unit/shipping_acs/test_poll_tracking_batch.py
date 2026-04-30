"""Verify the distributed lock guarding ``poll_acs_tracking_batch``.

ACS rate-limits at 10 req/sec. The batch task fans out to per-shipment
sub-tasks via ``apply_async(countdown=...)``. Two Celery workers
running the same beat tick would both dispatch the full N-task fan-out
— doubling the API rate. We guard with a Redis-backed ``cache.add``
mutex; this test suite verifies the guard fires.

DummyCache (used in unit tests per project memory) returns True for
every ``add()`` call, which means the lock-blocked branch is otherwise
never exercised. We patch ``cache.add`` directly to simulate the
"another worker holds the lock" state.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from shipping_acs.tasks import poll_acs_tracking_batch

pytestmark = pytest.mark.django_db


def test_batch_skips_when_lock_held():
    """When ``cache.add`` returns False (another worker holds the
    lock), the task short-circuits without dispatching any sub-tasks
    and reports skipped=True."""
    with (
        patch("django.core.cache.cache.add", return_value=False) as mock_add,
        patch(
            "shipping_acs.tasks.poll_acs_tracking_one.apply_async",
            return_value=None,
        ) as mock_dispatch,
    ):
        result = poll_acs_tracking_batch.run()

    assert mock_add.called
    assert result == {"dispatched": 0, "skipped": True}
    # No sub-tasks dispatched while the lock was held.
    assert not mock_dispatch.called


def test_batch_releases_lock_after_success():
    """When the task completes successfully it deletes the cache key
    so the next 15-min tick can acquire afresh."""
    with (
        patch("django.core.cache.cache.add", return_value=True),
        patch("django.core.cache.cache.delete") as mock_delete,
        patch(
            "shipping_acs.tasks.poll_acs_tracking_one.apply_async",
            return_value=None,
        ),
    ):
        result = poll_acs_tracking_batch.run()

    assert mock_delete.called
    assert mock_delete.call_args.args[0] == "acs:poll_batch:lock"
    assert result.get("skipped") is not True
