"""Unit tests for BoxNowService.

Real DB writes are used where needed (shipment creation, parcel events).
External HTTP calls (BoxNowClient) are always mocked.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

from order.enum.status import OrderStatus, PaymentStatus
from order.factories import OrderFactory
from shipping_boxnow.enum.parcel_state import BoxNowParcelState
from shipping_boxnow.exceptions import BoxNowAPIError
from shipping_boxnow.factories import (
    BoxNowLockerFactory,
    BoxNowShipmentFactory,
)
from shipping_boxnow.models import BoxNowParcelEvent
from shipping_boxnow.services import BoxNowService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BOXNOW_API_RESPONSE = {
    "id": "42224",
    "parcels": [{"id": "9219709201"}],
}


def _mock_client_create_ok():
    """Return a mock BoxNowClient that succeeds on create_delivery_request."""
    mock = MagicMock()
    mock.return_value.create_delivery_request.return_value = (
        _BOXNOW_API_RESPONSE
    )
    return mock


def _build_envelope(
    parcel_id: str,
    event: str,
    message_id: str | None = None,
    event_time: str | None = None,
) -> dict:
    """Build a minimal CloudEvents envelope for apply_webhook_event."""
    if message_id is None:
        import uuid

        message_id = str(uuid.uuid4())
    if event_time is None:
        event_time = timezone.now().isoformat()
    return {
        "specversion": "1.0",
        "type": "gr.boxnow.parcel_event_change",
        "source": "boxnow",
        "subject": parcel_id,
        "id": message_id,
        "time": event_time,
        "datacontenttype": "application/json",
        "datasignature": "ignored",
        "data": {
            "parcelId": parcel_id,
            "parcelState": event,
            "event": event,
            "time": event_time,
            "orderNumber": "ORD-001",
            "eventLocation": {
                "displayName": "Test Locker",
                "postalCode": "11523",
            },
            "customer": {
                "name": "John Doe",
                "email": "j@test.com",
                "phone": "+302100000000",
            },
        },
    }


# ---------------------------------------------------------------------------
# create_shipment_for_order
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCreateShipmentForOrder:
    def test_success_sets_ids_on_shipment_and_order(self):
        """After a successful API call, IDs are persisted on shipment and order."""
        order = OrderFactory(
            status=OrderStatus.PROCESSING,
            payment_status=PaymentStatus.COMPLETED,
        )
        BoxNowShipmentFactory(
            order=order,
            locker_external_id="4",
            parcel_state=BoxNowParcelState.PENDING_CREATION,
        )

        with patch(
            "shipping_boxnow.services.BoxNowClient",
            _mock_client_create_ok(),
        ):
            result = BoxNowService.create_shipment_for_order(order)

        result.refresh_from_db()
        assert result.delivery_request_id == "42224"
        assert result.parcel_id == "9219709201"
        assert result.parcel_state == BoxNowParcelState.NEW

        order.refresh_from_db()
        assert order.tracking_number == "9219709201"
        assert order.shipping_carrier == "boxnow"

    def test_idempotent_when_delivery_request_already_set(self):
        """Second call returns existing shipment without calling BoxNow API."""
        order = OrderFactory(status=OrderStatus.PROCESSING)
        BoxNowShipmentFactory(
            order=order,
            delivery_request_id="42224",
            parcel_id="9219709201",
            parcel_state=BoxNowParcelState.NEW,
        )

        mock_client_cls = MagicMock()
        with patch("shipping_boxnow.services.BoxNowClient", mock_client_cls):
            result = BoxNowService.create_shipment_for_order(order)

        # Client should never be instantiated when idempotency guard fires.
        mock_client_cls.assert_not_called()
        assert result.delivery_request_id == "42224"

    def test_propagates_boxnow_api_error(self):
        """BoxNowAPIError from the client bubbles up to the caller."""
        order = OrderFactory(status=OrderStatus.PROCESSING)
        BoxNowShipmentFactory(
            order=order,
            parcel_state=BoxNowParcelState.PENDING_CREATION,
        )

        api_error = BoxNowAPIError(
            400, code="P402", message="Invalid destination"
        )
        mock_client = MagicMock()
        mock_client.return_value.create_delivery_request.side_effect = api_error

        with (
            patch("shipping_boxnow.services.BoxNowClient", mock_client),
            pytest.raises(BoxNowAPIError) as exc_info,
        ):
            BoxNowService.create_shipment_for_order(order)

        assert exc_info.value.code == "P402"


# ---------------------------------------------------------------------------
# apply_webhook_event
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestApplyWebhookEvent:
    def test_creates_parcel_event_and_updates_shipment_state(self):
        """apply_webhook_event creates a BoxNowParcelEvent and updates state."""
        shipment = BoxNowShipmentFactory(with_parcel=True)
        envelope = _build_envelope(
            parcel_id=shipment.parcel_id,
            event="in-depot",
        )

        event = BoxNowService.apply_webhook_event(envelope)

        assert event is not None
        assert event.event_type == BoxNowParcelState.IN_DEPOT
        assert event.shipment_id == shipment.pk

        shipment.refresh_from_db()
        assert shipment.parcel_state == BoxNowParcelState.IN_DEPOT
        assert shipment.last_event_at is not None

    def test_idempotent_via_message_id(self):
        """Sending the same envelope twice creates only one ParcelEvent."""
        shipment = BoxNowShipmentFactory(with_parcel=True)
        message_id = "idempotency-test-msg"
        envelope = _build_envelope(
            parcel_id=shipment.parcel_id,
            event="in-depot",
            message_id=message_id,
        )

        result_1 = BoxNowService.apply_webhook_event(envelope)
        result_2 = BoxNowService.apply_webhook_event(envelope)

        assert result_1 is not None
        assert result_2 is None  # second call is a no-op

        count = BoxNowParcelEvent.objects.filter(
            webhook_message_id=message_id
        ).count()
        assert count == 1

    def test_handles_unknown_event_type_gracefully(self):
        """An unrecognised event string is stored without raising."""
        shipment = BoxNowShipmentFactory(with_parcel=True)
        envelope = _build_envelope(
            parcel_id=shipment.parcel_id,
            event="mystery-event-xyz",
        )
        # Must not raise even for unknown event. Return value may be None
        # (no shipment state update) or a ParcelEvent with a raw string in
        # event_type — either is acceptable; the test just asserts no
        # exception escapes.
        BoxNowService.apply_webhook_event(envelope)

    def test_returns_none_for_unknown_parcel_id(self):
        """Webhook for a parcel_id we've never seen returns None, no error."""
        envelope = _build_envelope(
            parcel_id="0000000000",
            event="new",
        )
        result = BoxNowService.apply_webhook_event(envelope)
        assert result is None

    def test_final_destination_transitions_order_to_shipped(self):
        """final-destination event advances order status to SHIPPED."""
        order = OrderFactory(
            status=OrderStatus.PROCESSING,
            payment_status=PaymentStatus.COMPLETED,
        )
        shipment = BoxNowShipmentFactory(
            order=order,
            with_parcel=True,
            parcel_state=BoxNowParcelState.IN_DEPOT,
        )

        # Patch the arrival notification task .delay so the eagerly-executed
        # task body (email template + DB) does not run and fail in unit tests.
        # The import inside apply_webhook_event is local, so we patch the
        # task object on the tasks module directly.
        with patch(
            "shipping_boxnow.tasks.boxnow_send_arrival_notification.delay"
        ):
            envelope = _build_envelope(
                parcel_id=shipment.parcel_id,
                event="final-destination",
            )
            BoxNowService.apply_webhook_event(envelope)

        order.refresh_from_db()
        assert order.status == OrderStatus.SHIPPED

    def test_delivered_transitions_order_to_delivered(self):
        """delivered event advances order status to DELIVERED."""
        order = OrderFactory(
            status=OrderStatus.SHIPPED,
            payment_status=PaymentStatus.COMPLETED,
        )
        shipment = BoxNowShipmentFactory(
            order=order,
            with_parcel=True,
            parcel_state=BoxNowParcelState.FINAL_DESTINATION,
        )

        envelope = _build_envelope(
            parcel_id=shipment.parcel_id,
            event="delivered",
        )
        BoxNowService.apply_webhook_event(envelope)

        order.refresh_from_db()
        assert order.status == OrderStatus.DELIVERED


# ---------------------------------------------------------------------------
# cancel_shipment
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCancelShipment:
    def test_cancel_when_new_succeeds(self):
        """cancel_shipment succeeds when parcel_state is NEW."""
        shipment = BoxNowShipmentFactory(
            with_parcel=True,
            parcel_state=BoxNowParcelState.NEW,
        )

        mock_client = MagicMock()
        with patch("shipping_boxnow.services.BoxNowClient", mock_client):
            BoxNowService.cancel_shipment(shipment, reason="Customer request")

        mock_client.return_value.cancel_parcel.assert_called_once_with(
            shipment.parcel_id
        )
        shipment.refresh_from_db()
        assert shipment.parcel_state == BoxNowParcelState.CANCELED
        assert shipment.cancel_requested_at is not None

    def test_cancel_on_delivered_raises_p420(self):
        """cancel_shipment raises BoxNowAPIError(P420) for terminal state."""
        shipment = BoxNowShipmentFactory(
            with_parcel=True,
            parcel_state=BoxNowParcelState.DELIVERED,
        )

        mock_client = MagicMock()
        with (
            patch("shipping_boxnow.services.BoxNowClient", mock_client),
            pytest.raises(BoxNowAPIError) as exc_info,
        ):
            BoxNowService.cancel_shipment(shipment)

        assert exc_info.value.code == "P420"
        mock_client.return_value.cancel_parcel.assert_not_called()


# ---------------------------------------------------------------------------
# sync_lockers
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSyncLockers:
    def test_creates_new_and_deactivates_stale_lockers(self):
        """sync_lockers creates new rows and marks absent rows inactive."""
        # Pre-create lockers: 'a' (will be updated) and 'c' (will be stale).
        BoxNowLockerFactory(external_id="a", is_active=True)
        BoxNowLockerFactory(external_id="c", is_active=True)

        api_destinations = [
            {
                "id": "a",
                "locationType": "apm",
                "name": "Locker A updated",
                "title": "A",
                "lat": "37.9750",
                "lng": "23.7350",
                "address": {
                    "addressLine1": "Addr A",
                    "postalCode": "11521",
                    "countryCode": "GR",
                },
            },
            {
                "id": "b",
                "locationType": "apm",
                "name": "Locker B new",
                "title": "B",
                "lat": "37.9810",
                "lng": "23.7300",
                "address": {
                    "addressLine1": "Addr B",
                    "postalCode": "11522",
                    "countryCode": "GR",
                },
            },
        ]

        mock_client = MagicMock()
        mock_client.return_value.list_destinations.return_value = (
            api_destinations
        )

        with patch("shipping_boxnow.services.BoxNowClient", mock_client):
            stats = BoxNowService.sync_lockers()

        from shipping_boxnow.models import BoxNowLocker

        assert BoxNowLocker.objects.filter(
            external_id="a", is_active=True
        ).exists()
        assert BoxNowLocker.objects.filter(
            external_id="b", is_active=True
        ).exists()
        assert BoxNowLocker.objects.filter(
            external_id="c", is_active=False
        ).exists()

        assert stats["created"] == 1  # 'b'
        assert stats["updated"] == 1  # 'a'
        assert stats["deactivated"] == 1  # 'c'
