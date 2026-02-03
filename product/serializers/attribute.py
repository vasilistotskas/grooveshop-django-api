from drf_spectacular.utils import extend_schema_field
from parler_rest.serializers import TranslatableModelSerializer
from rest_framework import serializers

from core.api.schema import generate_schema_multi_lang
from core.utils.serializers import TranslatedFieldExtended
from product.models.attribute import Attribute


@extend_schema_field(generate_schema_multi_lang(Attribute))
class TranslatedFieldsFieldExtend(TranslatedFieldExtended):
    pass


class AttributeSerializer(TranslatableModelSerializer):
    """Serializer for Attribute with translations."""

    translations = TranslatedFieldsFieldExtend(shared_model=Attribute)
    values_count = serializers.IntegerField(read_only=True, required=False)
    usage_count = serializers.IntegerField(read_only=True, required=False)

    class Meta:
        model = Attribute
        fields = [
            "id",
            "uuid",
            "translations",
            "active",
            "sort_order",
            "values_count",
            "usage_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "uuid",
            "sort_order",
            "values_count",
            "usage_count",
            "created_at",
            "updated_at",
        ]

    def validate_translations(self, value):
        """Validate that at least the default language translation exists."""
        if not value:
            raise serializers.ValidationError(
                "At least one translation is required."
            )

        # Check if default language (English) has a name
        if "en" in value and not value["en"].get("name"):
            raise serializers.ValidationError(
                "Default language (English) translation is required."
            )

        return value
