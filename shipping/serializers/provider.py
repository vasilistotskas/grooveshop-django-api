from drf_spectacular.openapi import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from shipping.models import ShippingProvider


class ShippingProviderSerializer(serializers.ModelSerializer[ShippingProvider]):
    logo = serializers.ImageField(
        read_only=True,
        help_text=(
            "Absolute URL for the operator-uploaded primary brand "
            "logo (home-delivery rows + fallback for pickup_point "
            "when no pickup-specific variant is set). ``null`` when "
            "no upload — the storefront falls back to its bundled "
            "default."
        ),
    )
    logo_pickup_point = serializers.ImageField(
        read_only=True,
        help_text=(
            "Absolute URL for the optional pickup-point-specific "
            "logo (e.g. a locker illustration distinct from the "
            "carrier's home-delivery brand mark). Surfaced on the "
            "pickup_point row's ``logoUrl`` when uploaded; otherwise "
            "the row falls back to ``logo``."
        ),
    )
    main_image_path = serializers.SerializerMethodField(
        help_text=(
            "Relative ``media/uploads/shipping/<filename>`` path for "
            "the primary logo; empty string when no logo is uploaded. "
            "Mirrors the PayWay.icon contract."
        ),
    )
    logo_filename = serializers.SerializerMethodField(
        help_text="Filename of the primary uploaded logo (or empty).",
    )
    logo_pickup_point_filename = serializers.SerializerMethodField(
        help_text="Filename of the pickup-point logo (or empty).",
    )

    @extend_schema_field(OpenApiTypes.STR)
    def get_main_image_path(self, obj: ShippingProvider) -> str:
        return obj.main_image_path

    @extend_schema_field(OpenApiTypes.STR)
    def get_logo_filename(self, obj: ShippingProvider) -> str:
        return obj.logo_filename

    @extend_schema_field(OpenApiTypes.STR)
    def get_logo_pickup_point_filename(self, obj: ShippingProvider) -> str:
        return obj.logo_pickup_point_filename

    class Meta:
        model = ShippingProvider
        fields = (
            "id",
            "code",
            "name",
            "is_active",
            "supports_home_delivery",
            "supports_pickup_point",
            "live_mode",
            "priority",
            "logo",
            "logo_pickup_point",
            "main_image_path",
            "logo_filename",
            "logo_pickup_point_filename",
            "metadata",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields
