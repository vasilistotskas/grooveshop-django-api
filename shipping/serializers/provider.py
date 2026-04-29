from rest_framework import serializers

from shipping.models import ShippingProvider


class ShippingProviderSerializer(serializers.ModelSerializer[ShippingProvider]):
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
            "metadata",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields
