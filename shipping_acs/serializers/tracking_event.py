from rest_framework import serializers

from shipping_acs.models import AcsTrackingEvent


class AcsTrackingEventSerializer(serializers.ModelSerializer[AcsTrackingEvent]):
    class Meta:
        model = AcsTrackingEvent
        fields = (
            "id",
            "event_time",
            "checkpoint_action",
            "checkpoint_location",
            "notes",
            "received_at",
        )
        read_only_fields = fields
