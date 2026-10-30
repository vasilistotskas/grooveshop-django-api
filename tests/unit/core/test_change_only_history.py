"""A save that only touches excluded fields leaves no history row.

``HistoricalRecords(excluded_fields=...)`` alone still writes a row per
save: the carrier tracking poll wrote one per shipment per tick.
"""

from __future__ import annotations

import pytest
from django.utils import timezone

from shipping_acs.enum.shipment_state import AcsShipmentState
from shipping_acs.factories import AcsShipmentFactory
from shipping_boxnow.enum.parcel_state import BoxNowParcelState
from shipping_boxnow.factories import BoxNowShipmentFactory

pytestmark = pytest.mark.django_db


class TestAcsShipmentHistory:
    def test_a_poll_tick_writes_no_history_row(self):
        shipment = AcsShipmentFactory(with_voucher=True)
        before = shipment.history.count()

        shipment.last_polled_at = timezone.now()
        shipment.metadata = {"polled": True}
        shipment.stale_alert_sent = True
        shipment.save(
            update_fields=[
                "last_polled_at",
                "metadata",
                "stale_alert_sent",
                "updated_at",
            ]
        )

        assert shipment.history.count() == before

    def test_a_state_change_still_writes_one(self):
        shipment = AcsShipmentFactory(with_voucher=True)
        before = shipment.history.count()

        shipment.shipment_state = AcsShipmentState.IN_TRANSIT
        shipment.last_polled_at = timezone.now()
        shipment.save(update_fields=["shipment_state", "last_polled_at"])

        assert shipment.history.count() == before + 1
        assert shipment.history.first().shipment_state == (
            AcsShipmentState.IN_TRANSIT
        )

    def test_a_full_save_writes_one(self):
        shipment = AcsShipmentFactory(with_voucher=True)
        before = shipment.history.count()

        shipment.save()

        assert shipment.history.count() == before + 1

    def test_creation_writes_one(self):
        shipment = AcsShipmentFactory(with_voucher=True)

        assert shipment.history.count() == 1


class TestBoxNowShipmentHistory:
    def test_a_poll_tick_writes_no_history_row(self):
        shipment = BoxNowShipmentFactory(with_parcel=True)
        before = shipment.history.count()

        shipment.last_polled_at = timezone.now()
        shipment.last_event_at = timezone.now()
        shipment.save(
            update_fields=["last_polled_at", "last_event_at", "updated_at"]
        )

        assert shipment.history.count() == before

    def test_a_state_change_still_writes_one(self):
        shipment = BoxNowShipmentFactory(with_parcel=True)
        before = shipment.history.count()

        shipment.parcel_state = BoxNowParcelState.DELIVERED
        shipment.save(update_fields=["parcel_state", "last_polled_at"])

        assert shipment.history.count() == before + 1
