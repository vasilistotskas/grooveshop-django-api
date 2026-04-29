from rest_framework import serializers

from shipping_acs.models import AcsStation


class AcsStationSerializer(serializers.ModelSerializer[AcsStation]):
    """List serializer for ACS stations / Smartpoints.

    Includes ``working_hours`` so the checkout picker can render
    opening hours inline without requiring a per-row detail fetch.
    """

    class Meta:
        model = AcsStation
        fields = (
            "id",
            "uuid",
            "external_id",
            "branch_code",
            "shop_kind",
            "name",
            "address_line_1",
            "city",
            "postal_code",
            "country_code",
            "lat",
            "lng",
            "max_weight_kg",
            "working_hours",
            "is_active",
            "last_synced_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class AcsStationDetailSerializer(AcsStationSerializer):
    class Meta(AcsStationSerializer.Meta):
        fields = (
            *AcsStationSerializer.Meta.fields,
            "address_line_2",
            "region",
            "phone",
        )
        read_only_fields = fields
