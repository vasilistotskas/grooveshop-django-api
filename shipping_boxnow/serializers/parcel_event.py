from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from shipping_boxnow.models.parcel_event import BoxNowParcelEvent


class BoxNowParcelEventSerializer(
    serializers.ModelSerializer[BoxNowParcelEvent]
):
    """
    Read-only serializer for ``BoxNowParcelEvent`` webhook audit records.

    All fields are read-only — events are written exclusively by the
    webhook handler and are never modified after creation.
    """

    event_type_display = serializers.SerializerMethodField(
        help_text=_("Human-readable label for the event_type choice"),
    )

    @extend_schema_field({"type": "string"})
    def get_event_type_display(self, instance: BoxNowParcelEvent) -> str:
        return instance.get_event_type_display()

    class Meta:
        model = BoxNowParcelEvent
        fields = (
            "id",
            "webhook_message_id",
            "event_type",
            "event_type_display",
            "parcel_state",
            "event_time",
            "display_name",
            "postal_code",
            "additional_information",
            "received_at",
            "created_at",
        )
        read_only_fields = fields
