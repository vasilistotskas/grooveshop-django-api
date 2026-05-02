from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from shipping_acs.models import AcsStation


class AcsStationSerializer(serializers.ModelSerializer[AcsStation]):
    """List serializer for ACS stations / Smartpoints.

    Includes ``working_hours`` so the checkout picker can render
    opening hours inline without requiring a per-row detail fetch.
    """

    # DRF's COERCE_DECIMAL_TO_STRING=True serialises DecimalField as a
    # JSON string, but drf-spectacular introspects the model field type
    # and emits ``number`` instead of ``string``.  Use SerializerMethodField
    # + @extend_schema_field so the generated OpenAPI spec matches the
    # actual wire format (strings, nullable for lat/lng).
    lat = serializers.SerializerMethodField()
    lng = serializers.SerializerMethodField()
    max_weight_kg = serializers.SerializerMethodField()

    @extend_schema_field({"type": "string", "nullable": True})
    def get_lat(self, obj: AcsStation) -> str | None:
        return str(obj.lat) if obj.lat is not None else None

    @extend_schema_field({"type": "string", "nullable": True})
    def get_lng(self, obj: AcsStation) -> str | None:
        return str(obj.lng) if obj.lng is not None else None

    @extend_schema_field({"type": "string"})
    def get_max_weight_kg(self, obj: AcsStation) -> str:
        return str(obj.max_weight_kg)

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
