from drf_spectacular.openapi import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from shipping.models import ShippingProvider


class ShippingProviderSerializer(serializers.ModelSerializer[ShippingProvider]):
    logo = serializers.ImageField(
        read_only=True,
        help_text=(
            "Absolute URL for the operator-uploaded brand logo. ``null`` "
            "when the operator hasn't uploaded one — the storefront "
            "falls back to its bundled default for the carrier. "
            "``settings.MEDIA_URL`` is absolute in every environment, "
            "so ``ImageField.url`` is always a full URL here."
        ),
    )
    main_image_path = serializers.SerializerMethodField(
        help_text=(
            "Relative ``media/uploads/shipping/<filename>`` path; "
            "empty string when no logo is uploaded. Mirrors the "
            "PayWay.icon contract so the storefront can use the same "
            "URL-building convention for both."
        ),
    )
    logo_filename = serializers.SerializerMethodField(
        help_text="Filename of the uploaded logo (or empty string).",
    )

    @extend_schema_field(OpenApiTypes.STR)
    def get_main_image_path(self, obj: ShippingProvider) -> str:
        return obj.main_image_path

    @extend_schema_field(OpenApiTypes.STR)
    def get_logo_filename(self, obj: ShippingProvider) -> str:
        return obj.logo_filename

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
            "main_image_path",
            "logo_filename",
            "metadata",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields
