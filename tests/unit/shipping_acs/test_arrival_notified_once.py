"""The customer is told once that the parcel is arriving.

`_maybe_notify_arrival` fired on the `→ OUT_FOR_DELIVERY` edge, and the
poll's docstring claimed it "only updates `shipment_state` on forward
transitions". That was never true: `from_tracking_summary` protects only
the terminal states (DELIVERED / RETURNED / CANCELED / LOST), and ACS's
`shipment_status` is a snapshot of the parcel's current leg, not a
monotonic sequence.

So the ordinary overnight cycle — loaded on a vehicle (4), returned to
the depot at end of shift (3), loaded again next morning (4) — walked
OUT_FOR_DELIVERY → AT_DESTINATION → OUT_FOR_DELIVERY and notified the
customer on every re-entry:

    state now: out_for_delivery
    state now: at_destination
    state now: out_for_delivery
    ARRIVAL NOTIFICATIONS SENT: 2
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.db import connection
from kombu.exceptions import OperationalError

from order.enum.status import OrderStatus, PaymentStatus
from order.factories.order import OrderFactory
from shipping_acs import services
from shipping_acs.enum.shipment_state import AcsShipmentState
from shipping_acs.factories import AcsShipmentFactory
from shipping_acs.models.shipment import AcsShipment
from shipping_acs.services import AcsService
from shipping_acs.tasks import acs_send_arrival_notification
from tenant.models import DeferredDispatch

pytestmark = pytest.mark.django_db


def _client_returning(statuses):
    """An ACS client that walks the given `shipment_status` sequence."""
    remaining = iter(statuses)

    class _Client:
        billing_code = "TEST_BILLING"

        def tracking_summary(self, voucher_no):
            return {
                "shipment_status": next(remaining),
                "delivery_flag": "0",
                "returned_flag": "0",
            }

        def tracking_details(self, voucher_no):
            return []

    return _Client


# The main lane runs single-schema on `public`; the dispatch pins
# whatever `connection.schema_name` is at registration.
SCHEMA = connection.schema_name


@pytest.fixture
def shipment(db):
    order = OrderFactory(
        status=OrderStatus.PROCESSING, payment_status=PaymentStatus.COMPLETED
    )
    return AcsShipmentFactory(
        order=order,
        voucher_no="9001999100",
        shipment_state=AcsShipmentState.IN_TRANSIT,
    )


def test_the_overnight_depot_cycle_notifies_once(shipment, monkeypatch):
    monkeypatch.setattr(
        services, "AcsClient", _client_returning(["4", "3", "4"])
    )

    with patch(
        "shipping_acs.tasks.acs_send_arrival_notification.apply_async"
    ) as notify:
        for _ in range(3):
            AcsService.poll_shipment_tracking(shipment)

    assert notify.call_count == 1

    shipment.refresh_from_db()
    assert shipment.arrival_notified_at is not None


def test_the_first_arrival_still_notifies(shipment, monkeypatch):
    monkeypatch.setattr(services, "AcsClient", _client_returning(["4"]))

    with patch(
        "shipping_acs.tasks.acs_send_arrival_notification.apply_async"
    ) as notify:
        AcsService.poll_shipment_tracking(shipment)

    notify.assert_called_once_with(
        args=[shipment.id], headers={"_schema_name": SCHEMA}
    )


def test_a_parcel_that_never_goes_out_is_never_notified(shipment, monkeypatch):
    monkeypatch.setattr(services, "AcsClient", _client_returning(["2", "3"]))

    with patch(
        "shipping_acs.tasks.acs_send_arrival_notification.apply_async"
    ) as notify:
        for _ in range(2):
            AcsService.poll_shipment_tracking(shipment)

    notify.assert_not_called()
    shipment.refresh_from_db()
    assert shipment.arrival_notified_at is None


def test_the_state_still_follows_acs_backwards(shipment, monkeypatch):
    """The fix is on the notification, not on the state.

    A failed delivery attempt genuinely puts the parcel back at the
    depot. Freezing `shipment_state` at OUT_FOR_DELIVERY to suppress the
    duplicate would make the admin and the customer's tracking page
    report something untrue.
    """
    monkeypatch.setattr(services, "AcsClient", _client_returning(["4", "3"]))

    with patch("shipping_acs.tasks.acs_send_arrival_notification.apply_async"):
        for _ in range(2):
            AcsService.poll_shipment_tracking(shipment)

    shipment.refresh_from_db()
    assert shipment.shipment_state == AcsShipmentState.AT_DESTINATION


def test_the_marker_is_not_written_when_nothing_was_sent(shipment):
    """A poll that never reaches OUT_FOR_DELIVERY must leave it null."""
    assert AcsShipment.objects.get(pk=shipment.pk).arrival_notified_at is None


def test_a_failed_broker_handoff_is_kept_and_marked_not_sent_twice(
    shipment, monkeypatch
):
    """A publish the broker refuses is kept, so the mark may be written.

    `dispatch_on_commit` turns a `kombu` `OperationalError` into a
    `DeferredDispatch` row that the sweep publishes again, so the
    notification is owed and will go out. The mark stops the next depot
    cycle from queueing a second notification while the first waits in the
    table. (A bare `OSError`, which the helper does not catch, would be a
    bug and still reaches the poller.)
    """
    monkeypatch.setattr(
        services, "AcsClient", _client_returning(["4", "3", "4"])
    )

    with patch(
        "shipping_acs.tasks.acs_send_arrival_notification.apply_async",
        side_effect=OperationalError("broker unreachable"),
    ) as notify:
        AcsService.poll_shipment_tracking(shipment)

    notify.assert_called_once()
    row = DeferredDispatch.objects.get()
    assert row.task_name == acs_send_arrival_notification.name
    assert row.args == [shipment.id]
    assert row.headers == {"_schema_name": SCHEMA}

    shipment.refresh_from_db()
    assert shipment.arrival_notified_at is not None, (
        "the notification is owed through the kept row, so the next poll "
        "must not queue another"
    )

    # The next depot cycle back out for delivery sends no second one.
    with patch(
        "shipping_acs.tasks.acs_send_arrival_notification.apply_async"
    ) as notify:
        for _ in range(2):
            AcsService.poll_shipment_tracking(shipment)

    notify.assert_not_called()
    assert DeferredDispatch.objects.count() == 1


def test_a_notification_neither_sent_nor_kept_is_not_marked(
    shipment, monkeypatch
):
    """The mark says it is owed: nothing owes it when no row was written."""
    monkeypatch.setattr(services, "AcsClient", _client_returning(["4"]))

    with (
        patch(
            "shipping_acs.tasks.acs_send_arrival_notification.apply_async",
            side_effect=OperationalError("broker unreachable"),
        ),
        patch.object(
            DeferredDispatch.objects, "create", side_effect=RuntimeError
        ),
    ):
        AcsService.poll_shipment_tracking(shipment)

    shipment.refresh_from_db()
    assert shipment.arrival_notified_at is None


def test_a_publish_bug_still_reaches_the_poller_and_marks_nothing(
    shipment, monkeypatch
):
    """Only a network failure is absorbed: anything else is a bug."""
    monkeypatch.setattr(services, "AcsClient", _client_returning(["4"]))

    with (
        patch(
            "shipping_acs.tasks.acs_send_arrival_notification.apply_async",
            side_effect=TypeError("bad arguments"),
        ),
        pytest.raises(TypeError),
    ):
        AcsService.poll_shipment_tracking(shipment)

    shipment.refresh_from_db()
    assert shipment.arrival_notified_at is None
    assert not DeferredDispatch.objects.exists()
