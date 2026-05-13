"""Tests for BoxNowService.sync_shipment_state (poll fallback).

These tests lock the defence-in-depth contract: even if BoxNow's
partner-managed webhook URL is unregistered, misconfigured, or
delivery is silently dropped, a periodic poll of GET /api/v1/parcels
converges shipment state. The poll path must be idempotent with
itself AND with the webhook path — duplicate rows for the same
``(shipment, event_type, event_time)`` are never written.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from django.utils.dateparse import parse_datetime

from order.factories import OrderFactory
from shipping_boxnow.enum.parcel_state import BoxNowParcelState
from shipping_boxnow.factories import BoxNowShipmentFactory
from shipping_boxnow.models import BoxNowParcelEvent
from shipping_boxnow.services import BoxNowService


def _parcel_payload(
    parcel_id: str,
    state: str,
    *,
    events: list[dict],
) -> dict:
    """Build a ``/api/v1/parcels`` response with the given event list."""
    return {
        "data": [
            {
                "id": parcel_id,
                "state": state,
                "events": events,
            }
        ],
        "pagination": {},
    }


@pytest.mark.django_db
class TestSyncShipmentState:
    def _shipment(self, parcel_id: str = "1234567890"):
        order = OrderFactory()
        return BoxNowShipmentFactory(
            order=order,
            parcel_id=parcel_id,
            delivery_request_id=f"dr-{parcel_id}",
            parcel_state=BoxNowParcelState.NEW,
        )

    def test_no_parcel_id_short_circuits(self):
        order = OrderFactory()
        shipment = BoxNowShipmentFactory(
            order=order, parcel_id=None, delivery_request_id=None
        )

        with patch("shipping_boxnow.services.BoxNowClient") as mock_cls:
            result = BoxNowService.sync_shipment_state(shipment)

        mock_cls.assert_not_called()
        assert result["events_synced"] == 0

    def test_first_poll_inserts_new_events_and_advances_state(self):
        shipment = self._shipment()
        payload = _parcel_payload(
            parcel_id=shipment.parcel_id,
            state="in-transit",
            events=[
                {
                    "type": "new",
                    "createTime": "2026-05-12T22:18:06.654Z",
                },
                {
                    "type": "accepted-to-locker",
                    "locationDisplayName": "ΚΛΙΝΗΣ Ελαστικά - Μαρούσι",
                    "postalCode": "15126",
                    "createTime": "2026-05-13T19:08:30.495Z",
                },
            ],
        )
        mock_client = MagicMock()
        mock_client.return_value.get_parcel_info.return_value = payload

        with patch("shipping_boxnow.services.BoxNowClient", mock_client):
            result = BoxNowService.sync_shipment_state(shipment)

        assert result["events_synced"] == 2
        events = list(BoxNowParcelEvent.objects.filter(shipment=shipment))
        assert {e.event_type for e in events} == {
            BoxNowParcelState.NEW,
            BoxNowParcelState.ACCEPTED_TO_LOCKER,
        }

        shipment.refresh_from_db()
        assert shipment.parcel_state == BoxNowParcelState.ACCEPTED_TO_LOCKER
        assert shipment.last_event_at == parse_datetime(
            "2026-05-13T19:08:30.495Z"
        )
        assert shipment.last_polled_at is not None

    def test_second_poll_is_idempotent(self):
        """Re-polling with the same response yields zero new events."""
        shipment = self._shipment()
        payload = _parcel_payload(
            parcel_id=shipment.parcel_id,
            state="in-transit",
            events=[
                {
                    "type": "new",
                    "createTime": "2026-05-12T22:18:06.654Z",
                }
            ],
        )
        mock_client = MagicMock()
        mock_client.return_value.get_parcel_info.return_value = payload

        with patch("shipping_boxnow.services.BoxNowClient", mock_client):
            first = BoxNowService.sync_shipment_state(shipment)
            second = BoxNowService.sync_shipment_state(shipment)

        assert first["events_synced"] == 1
        assert second["events_synced"] == 0
        assert BoxNowParcelEvent.objects.filter(shipment=shipment).count() == 1

    def test_does_not_duplicate_existing_webhook_event(self):
        """A real webhook event already on file must not be re-inserted by
        the poll under a synthetic ``poll:`` id.

        Locks the (shipment, event_type, event_time) dedup contract.
        """
        shipment = self._shipment()
        event_time = parse_datetime("2026-05-13T19:08:30.495Z")
        BoxNowParcelEvent.objects.create(
            shipment=shipment,
            webhook_message_id="real-boxnow-cloudevents-id",
            event_type=BoxNowParcelState.ACCEPTED_TO_LOCKER,
            parcel_state="in-transit",
            event_time=event_time,
            display_name="ΚΛΙΝΗΣ Ελαστικά - Μαρούσι",
            postal_code="15126",
            raw_payload={"source": "webhook"},
        )

        payload = _parcel_payload(
            parcel_id=shipment.parcel_id,
            state="in-transit",
            events=[
                {
                    "type": "accepted-to-locker",
                    "locationDisplayName": "ΚΛΙΝΗΣ Ελαστικά - Μαρούσι",
                    "postalCode": "15126",
                    "createTime": "2026-05-13T19:08:30.495Z",
                },
            ],
        )
        mock_client = MagicMock()
        mock_client.return_value.get_parcel_info.return_value = payload

        with patch("shipping_boxnow.services.BoxNowClient", mock_client):
            result = BoxNowService.sync_shipment_state(shipment)

        assert result["events_synced"] == 0
        assert BoxNowParcelEvent.objects.filter(shipment=shipment).count() == 1

    def test_out_of_order_event_does_not_walk_state_backwards(self):
        """An older event arriving later via poll must not regress state."""
        shipment = self._shipment()
        # Existing newer state already applied:
        newer_time = parse_datetime("2026-05-13T22:00:00Z")
        BoxNowParcelEvent.objects.create(
            shipment=shipment,
            webhook_message_id="newer-event",
            event_type=BoxNowParcelState.FINAL_DESTINATION,
            parcel_state="in-final-destination",
            event_time=newer_time,
            raw_payload={"source": "webhook"},
        )
        shipment.parcel_state = BoxNowParcelState.FINAL_DESTINATION
        shipment.last_event_at = newer_time
        shipment.save(update_fields=["parcel_state", "last_event_at"])

        # Poll surfaces an older transition we hadn't recorded.
        payload = _parcel_payload(
            parcel_id=shipment.parcel_id,
            state="in-final-destination",
            events=[
                {
                    "type": "new",
                    "createTime": "2026-05-12T20:00:00Z",
                },
            ],
        )
        mock_client = MagicMock()
        mock_client.return_value.get_parcel_info.return_value = payload

        with patch("shipping_boxnow.services.BoxNowClient", mock_client):
            BoxNowService.sync_shipment_state(shipment)

        shipment.refresh_from_db()
        assert shipment.parcel_state == BoxNowParcelState.FINAL_DESTINATION
        assert shipment.last_event_at == newer_time

    def test_empty_data_array_bumps_last_polled_at_without_error(self):
        """If BoxNow returns no parcel rows we still mark the shipment
        polled so the batch task doesn't spin on it."""
        shipment = self._shipment()
        mock_client = MagicMock()
        mock_client.return_value.get_parcel_info.return_value = {
            "data": [],
            "pagination": {},
        }

        with patch("shipping_boxnow.services.BoxNowClient", mock_client):
            result = BoxNowService.sync_shipment_state(shipment)

        assert result["events_synced"] == 0
        shipment.refresh_from_db()
        assert shipment.last_polled_at is not None
