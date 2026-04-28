"""Unit tests for BoxNow model factories and model properties."""

from __future__ import annotations

import pytest

from shipping_boxnow.enum.parcel_state import BoxNowParcelState
from shipping_boxnow.factories import (
    BoxNowLockerFactory,
    BoxNowParcelEventFactory,
    BoxNowShipmentFactory,
)


@pytest.mark.django_db
class TestBoxNowLockerFactory:
    def test_creates_active_locker(self):
        """Default factory produces an active locker."""
        locker = BoxNowLockerFactory()
        assert locker.pk is not None
        assert locker.is_active is True
        assert locker.country_code == "GR"
        assert locker.type == "apm"
        assert locker.last_synced_at is not None

    def test_external_id_is_unique_across_instances(self):
        """Sequence-generated external_id must be unique."""
        lockers = BoxNowLockerFactory.create_batch(5)
        external_ids = {locker.external_id for locker in lockers}
        assert len(external_ids) == 5


@pytest.mark.django_db
class TestBoxNowShipmentFactory:
    def test_default_parcel_state_is_pending_creation(self):
        """Fresh shipment has parcel_state=PENDING_CREATION and null IDs."""
        shipment = BoxNowShipmentFactory()
        assert shipment.parcel_state == BoxNowParcelState.PENDING_CREATION
        # null=True on these unique fields so multiple pending rows can
        # coexist before the BoxNow API assigns real IDs.
        assert shipment.delivery_request_id is None
        assert shipment.parcel_id is None
        assert shipment.order is not None

    def test_with_parcel_trait_sets_ids(self):
        """with_parcel trait populates delivery_request_id and parcel_id."""
        shipment = BoxNowShipmentFactory(with_parcel=True)
        assert shipment.delivery_request_id
        assert shipment.parcel_id
        assert shipment.parcel_state == BoxNowParcelState.NEW


@pytest.mark.django_db
class TestBoxNowParcelEventFactory:
    def test_creates_event_with_unique_message_id(self):
        """webhook_message_id is unique per the sequence."""
        e1 = BoxNowParcelEventFactory()
        e2 = BoxNowParcelEventFactory()
        assert e1.webhook_message_id != e2.webhook_message_id

    def test_event_is_linked_to_shipment(self):
        """Event FK to shipment is set."""
        event = BoxNowParcelEventFactory()
        assert event.shipment_id is not None


@pytest.mark.django_db
class TestBoxNowShipmentIsActive:
    """Test the is_active property on BoxNowShipment."""

    def test_pending_creation_is_active(self):
        shipment = BoxNowShipmentFactory(
            parcel_state=BoxNowParcelState.PENDING_CREATION
        )
        assert shipment.is_active is True

    def test_new_is_active(self):
        shipment = BoxNowShipmentFactory(parcel_state=BoxNowParcelState.NEW)
        assert shipment.is_active is True

    def test_delivered_is_not_active(self):
        shipment = BoxNowShipmentFactory(
            parcel_state=BoxNowParcelState.DELIVERED
        )
        assert shipment.is_active is False

    def test_canceled_is_not_active(self):
        shipment = BoxNowShipmentFactory(
            parcel_state=BoxNowParcelState.CANCELED
        )
        assert shipment.is_active is False

    def test_lost_is_not_active(self):
        shipment = BoxNowShipmentFactory(parcel_state=BoxNowParcelState.LOST)
        assert shipment.is_active is False

    def test_in_depot_is_active(self):
        shipment = BoxNowShipmentFactory(
            parcel_state=BoxNowParcelState.IN_DEPOT
        )
        assert shipment.is_active is True
