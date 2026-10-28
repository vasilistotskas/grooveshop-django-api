"""Verify the distributed lock guarding ``poll_acs_tracking_batch``.

The batch task fans out to per-shipment sub-tasks via
``apply_async(countdown=...)``. Two Celery workers running the same
beat tick would both dispatch the full N-task fan-out — doubling the
load on the ACS request slots. We guard with a Redis-backed ``cache.add``
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


def test_batch_skips_when_lock_held(acs_configured_tenant):
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


def test_batch_releases_lock_after_success(acs_configured_tenant):
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


def _dispatched_ids():
    with (
        patch("django.core.cache.cache.add", return_value=True),
        patch("django.core.cache.cache.delete"),
        patch(
            "shipping_acs.tasks.poll_acs_tracking_one.apply_async",
            return_value=None,
        ) as mock_dispatch,
    ):
        poll_acs_tracking_batch.run()
    return {call.kwargs["args"][0] for call in mock_dispatch.call_args_list}


def test_batch_skips_never_progressed_shipment_of_finished_order(
    acs_configured_tenant,
):
    """Prod order 56 (2026-10-28): COMPLETED since May, shipment still
    ``new`` with no tracking events, polled every 30 minutes for ever.
    A finished order has nothing left to track."""
    from order.enum.status import OrderStatus
    from order.factories.order import OrderFactory
    from shipping_acs.factories import AcsShipmentFactory

    open_shipment = AcsShipmentFactory(
        with_voucher=True,
        order=OrderFactory(status=OrderStatus.PROCESSING),
    )
    finished = [
        AcsShipmentFactory(with_voucher=True, order=OrderFactory(status=status))
        for status in (
            OrderStatus.COMPLETED,
            OrderStatus.CANCELED,
            OrderStatus.REFUNDED,
        )
    ]

    dispatched = _dispatched_ids()

    assert open_shipment.id in dispatched
    assert not dispatched & {s.id for s in finished}
