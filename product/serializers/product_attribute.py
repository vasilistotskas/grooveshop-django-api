from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from product.models.product_attribute import ProductAttribute


class ProductAttributeSerializer(serializers.ModelSerializer):
    """Serializer for ProductAttribute with nested attribute and value info."""

    attribute_id = serializers.IntegerField(
        source="attribute_value.attribute.id", read_only=True
    )
    attribute_name = serializers.SerializerMethodField()
    attribute_value_id = serializers.IntegerField(source="attribute_value.id")
    value = serializers.SerializerMethodField()

    class Meta:
        model = ProductAttribute
        fields = [
            "id",
            "attribute_id",
            "attribute_name",
            "attribute_value_id",
            "value",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "attribute_id",
            "attribute_name",
            "value",
            "created_at",
        ]

    def get_attribute_name(self, obj) -> str:
        """Return translated attribute name."""
        return obj.attribute_value.attribute.safe_translation_getter(
            "name", any_language=True
        )

    def get_value(self, obj) -> str:
        """Return translated attribute value."""
        return obj.attribute_value.safe_translation_getter(
            "value", any_language=True
        )

    def validate_attribute_value_id(self, value):
        """Validate that the attribute value exists and is active."""
        from product.models.attribute_value import AttributeValue

        try:
            attribute_value = AttributeValue.objects.get(id=value)
        except AttributeValue.DoesNotExist:
            raise serializers.ValidationError(
                _("Attribute value does not exist.")
            )

        if not attribute_value.active:
            raise serializers.ValidationError(
                _("Cannot assign inactive attribute value.")
            )

        if not attribute_value.attribute.active:
            raise serializers.ValidationError(
                _("Cannot assign value from inactive attribute.")
            )

        return value
