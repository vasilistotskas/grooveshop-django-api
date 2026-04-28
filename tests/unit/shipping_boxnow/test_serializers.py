"""Unit tests for BoxNow serializers."""

from __future__ import annotations

import pytest
from django.utils import timezone

from shipping_boxnow.factories import (
    BoxNowLockerFactory,
    BoxNowParcelEventFactory,
    BoxNowShipmentFactory,
)
from shipping_boxnow.serializers.locker import BoxNowLockerSerializer
from shipping_boxnow.serializers.shipment import BoxNowShipmentDetailSerializer


@pytest.mark.django_db
class TestBoxNowLockerSerializer:
    def test_round_trip_all_fields_present(self):
        """Serialising a locker includes all declared fields."""
        locker = BoxNowLockerFactory()
        data = BoxNowLockerSerializer(locker).data

        expected_fields = {
            "id",
            "external_id",
            "type",
            "image_url",
            "lat",
            "lng",
            "title",
            "name",
            "address_line_1",
            "address_line_2",
            "postal_code",
            "country_code",
            "note",
            "is_active",
            "last_synced_at",
            "created_at",
            "updated_at",
            "uuid",
        }
        assert expected_fields <= set(data.keys())

    def test_external_id_correct_type(self):
        """external_id serialises as a string."""
        locker = BoxNowLockerFactory(external_id="apm-0042")
        data = BoxNowLockerSerializer(locker).data
        assert data["external_id"] == "apm-0042"
        assert isinstance(data["external_id"], str)

    def test_is_active_boolean(self):
        """is_active serialises as a boolean."""
        locker = BoxNowLockerFactory(is_active=True)
        data = BoxNowLockerSerializer(locker).data
        assert data["is_active"] is True


@pytest.mark.django_db
class TestBoxNowShipmentDetailSerializer:
    def test_includes_nested_locker_when_set(self):
        """When locker FK is set, nested locker dict is present."""
        locker = BoxNowLockerFactory()
        shipment = BoxNowShipmentFactory(locker=locker, with_parcel=True)

        data = BoxNowShipmentDetailSerializer(shipment).data
        assert data["locker"] is not None
        assert data["locker"]["external_id"] == locker.external_id

    def test_locker_is_none_when_not_set(self):
        """When locker FK is None, nested locker is None."""
        shipment = BoxNowShipmentFactory(locker=None)
        data = BoxNowShipmentDetailSerializer(shipment).data
        assert data["locker"] is None

    def test_events_list_ordered_desc_by_event_time(self):
        """events list is ordered newest-first."""
        shipment = BoxNowShipmentFactory(with_parcel=True)
        now = timezone.now()
        from datetime import timedelta

        e_old = BoxNowParcelEventFactory(
            shipment=shipment,
            event_time=now - timedelta(hours=2),
        )
        e_new = BoxNowParcelEventFactory(
            shipment=shipment,
            event_time=now - timedelta(hours=1),
        )

        data = BoxNowShipmentDetailSerializer(shipment).data
        events = data["events"]
        assert len(events) == 2
        # Newest first.
        assert events[0]["webhook_message_id"] == e_new.webhook_message_id
        assert events[1]["webhook_message_id"] == e_old.webhook_message_id

    def test_events_capped_at_20(self):
        """Only up to 20 events are returned."""
        shipment = BoxNowShipmentFactory(with_parcel=True)
        BoxNowParcelEventFactory.create_batch(25, shipment=shipment)

        data = BoxNowShipmentDetailSerializer(shipment).data
        assert len(data["events"]) == 20

    def test_label_url_present_when_parcel_id_set(self):
        """label_url is a non-empty string when parcel_id is set."""
        shipment = BoxNowShipmentFactory(with_parcel=True)
        data = BoxNowShipmentDetailSerializer(shipment).data
        assert data["label_url"] is not None
        assert shipment.parcel_id in data["label_url"]

    def test_label_url_none_when_no_parcel_id(self):
        """label_url is None when parcel_id is blank."""
        shipment = BoxNowShipmentFactory(parcel_id="")
        data = BoxNowShipmentDetailSerializer(shipment).data
        assert data["label_url"] is None
