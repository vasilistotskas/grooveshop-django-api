"""Unit tests for ACS shipment serializers."""

from __future__ import annotations

import pytest

from shipping_acs.factories import AcsShipmentFactory
from shipping_acs.serializers.shipment import AcsShipmentDetailSerializer


@pytest.mark.django_db
class TestAcsShipmentDetailSerializer:
    def test_metadata_and_internal_flags_not_exposed_to_customer(self):
        """The customer-facing detail serializer (returned on the order-detail
        endpoint) must not expose internal metadata — which carries the ACS
        billing code, admin cancel reasons, and a failed-mint error envelope
        with recipient PII — nor the internal raw status / flags."""
        shipment = AcsShipmentFactory(
            with_voucher=True,
            metadata={
                "last_error": {"request_params": {"Recipient_Name": "Jane Doe"}}
            },
        )
        data = AcsShipmentDetailSerializer(shipment).data

        for hidden in (
            "metadata",
            "raw_shipment_status",
            "delivery_flag",
            "returned_flag",
        ):
            assert hidden not in data
