from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from shipping_acs.models import AcsShipment
from shipping_acs.serializers.station import AcsStationSerializer
from shipping_acs.serializers.tracking_event import (
    AcsTrackingEventSerializer,
)


class AcsShipmentSerializer(serializers.ModelSerializer[AcsShipment]):
    """Lightweight read-only serializer for ACS shipments."""

    shipment_state_display = serializers.SerializerMethodField(
        help_text=_("Human-readable label for the shipment_state choice"),
    )

    @extend_schema_field({"type": "string"})
    def get_shipment_state_display(self, obj: AcsShipment) -> str:
        return obj.get_shipment_state_display()

    class Meta:
        model = AcsShipment
        fields = (
            "id",
            "uuid",
            "voucher_no",
            "shipment_state",
            "shipment_state_display",
            "delivery_kind",
            "weight_grams",
            "item_quantity",
            "charge_type",
            "delivery_products",
            "last_event_at",
            "last_polled_at",
            "delivery_date",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class AcsShipmentDetailSerializer(AcsShipmentSerializer):
    """Detail serializer used on the order-detail endpoint."""

    station = serializers.SerializerMethodField(
        help_text=_("Destination ACS station details (Smartpoint pickups)."),
    )
    events = serializers.SerializerMethodField(
        help_text=_("Last 50 tracking events ordered by event_time desc."),
    )
    label_url = serializers.SerializerMethodField(
        help_text=_(
            "Relative URL to download the voucher PDF via the Django proxy."
        ),
    )

    @extend_schema_field(AcsStationSerializer(allow_null=True))
    def get_station(self, obj: AcsShipment) -> dict | None:
        if obj.station_destination_id is None:
            return None
        return AcsStationSerializer(
            obj.station_destination, context=self.context
        ).data

    @extend_schema_field(AcsTrackingEventSerializer(many=True))
    def get_events(self, obj: AcsShipment):
        qs = obj.events.order_by("-event_time")[:50]
        return AcsTrackingEventSerializer(
            qs, many=True, context=self.context
        ).data

    @extend_schema_field(
        {
            "type": "string",
            "nullable": True,
            "description": (
                "Relative URL for the ACS voucher label PDF; null when "
                "voucher_no is not yet assigned."
            ),
        }
    )
    def get_label_url(self, obj: AcsShipment) -> str | None:
        if not obj.voucher_no:
            return None
        return f"/api/v1/shipping/acs/shipments/{obj.voucher_no}/label.pdf"

    class Meta(AcsShipmentSerializer.Meta):
        fields = (
            *AcsShipmentSerializer.Meta.fields,
            "station_destination_external_id",
            "station_branch_destination",
            "station",
            "events",
            "label_url",
            "delivery_flag",
            "returned_flag",
            "raw_shipment_status",
            "cancel_requested_at",
            "metadata",
        )
        read_only_fields = fields
