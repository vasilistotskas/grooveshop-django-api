from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from shipping_boxnow.models.shipment import BoxNowShipment
from shipping_boxnow.serializers.locker import BoxNowLockerSerializer
from shipping_boxnow.serializers.parcel_event import (
    BoxNowParcelEventSerializer,
)


class BoxNowShipmentSerializer(serializers.ModelSerializer[BoxNowShipment]):
    """
    Lightweight serializer for BoxNow shipment list endpoints.

    All fields are read-only — shipments are managed exclusively by the
    service layer and Celery tasks.
    """

    parcel_state_display = serializers.SerializerMethodField(
        help_text=_("Human-readable label for the parcel_state choice"),
    )

    @extend_schema_field({"type": "string"})
    def get_parcel_state_display(self, obj: BoxNowShipment) -> str:
        return obj.get_parcel_state_display()

    class Meta:
        model = BoxNowShipment
        fields = (
            "id",
            "uuid",
            "delivery_request_id",
            "parcel_id",
            "locker_external_id",
            "parcel_state",
            "parcel_state_display",
            "compartment_size",
            "payment_mode",
            "last_event_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class BoxNowShipmentDetailSerializer(BoxNowShipmentSerializer):
    """
    Detail serializer for a single BoxNow shipment.

    Extends the list serializer with:
    - nested ``locker`` object (``BoxNowLockerSerializer``)
    - ``events`` — last 20 ``BoxNowParcelEvent`` records ordered by
      ``event_time`` descending
    - ``label_url`` — relative URL for downloading the parcel label PDF
      via the Django proxy route; ``None`` when ``parcel_id`` is blank

    Imports of ``BoxNowLockerSerializer`` and
    ``BoxNowParcelEventSerializer`` are deferred to method bodies to
    prevent circular import chains between the serializer modules.
    """

    locker = serializers.SerializerMethodField(
        help_text=_("Nested BoxNow locker details"),
    )
    events = serializers.SerializerMethodField(
        help_text=_("Last 20 parcel events ordered by event_time desc"),
    )
    label_url = serializers.SerializerMethodField(
        help_text=_(
            "Relative URL to download the parcel label PDF "
            "via the Django proxy endpoint"
        ),
    )

    @extend_schema_field(BoxNowLockerSerializer(allow_null=True))
    def get_locker(self, obj: BoxNowShipment) -> dict | None:
        if obj.locker_id is None:
            return None
        return BoxNowLockerSerializer(obj.locker, context=self.context).data

    @extend_schema_field(BoxNowParcelEventSerializer(many=True))
    def get_events(self, obj: BoxNowShipment):
        # Return type is DRF's ReturnDict-wrapped list, which is
        # iterable-as-list at the JSON layer; we omit the explicit
        # ``-> list`` annotation so ty doesn't complain about the
        # internal ReturnDict subtype.
        qs = obj.events.order_by("-event_time").select_related()[:20]
        return BoxNowParcelEventSerializer(
            qs, many=True, context=self.context
        ).data

    @extend_schema_field(
        {
            "type": "string",
            "nullable": True,
            "description": (
                "Relative URL for the parcel label PDF proxy; "
                "null when parcel_id is not yet assigned"
            ),
        }
    )
    def get_label_url(self, obj: BoxNowShipment) -> str | None:
        if not obj.parcel_id:
            return None
        return f"/api/v1/shipping/boxnow/parcels/{obj.parcel_id}/label.pdf"

    class Meta(BoxNowShipmentSerializer.Meta):
        fields = (
            *BoxNowShipmentSerializer.Meta.fields,
            "locker",
            "events",
            "label_url",
            "weight_grams",
            "amount_to_be_collected",
            "allow_return",
            "cancel_requested_at",
            "metadata",
        )
        read_only_fields = fields
