from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from parler_rest.serializers import TranslatableModelSerializer
from rest_framework import serializers
from rest_framework.relations import PrimaryKeyRelatedField

from core.api.schema import generate_schema_multi_lang
from core.utils.serializers import TranslatedFieldExtended
from product.models.attribute import Attribute
from product.models.attribute_value import AttributeValue


@extend_schema_field(generate_schema_multi_lang(AttributeValue))
class TranslatedFieldsFieldExtend(TranslatedFieldExtended):
    pass


class AttributeValueSerializer(TranslatableModelSerializer):
    """Serializer for AttributeValue with translations."""

    translations = TranslatedFieldsFieldExtend(shared_model=AttributeValue)
    attribute = PrimaryKeyRelatedField(queryset=Attribute.objects.all())
    attribute_name = serializers.SerializerMethodField()
    usage_count = serializers.IntegerField(read_only=True, required=False)

    class Meta:
        model = AttributeValue
        fields = [
            "id",
            "uuid",
            "attribute",
            "attribute_name",
            "translations",
            "active",
            "sort_order",
            "usage_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "uuid",
            "sort_order",
            "attribute_name",
            "usage_count",
            "created_at",
            "updated_at",
        ]

    def get_attribute_name(self, obj) -> str:
        """Return translated attribute name."""
        return obj.attribute.safe_translation_getter("name", any_language=True)

    def validate_attribute(self, value):
        """Validate that the attribute exists and is active."""
        if not value:
            raise serializers.ValidationError(_("Attribute is required."))

        if not value.active:
            raise serializers.ValidationError(
                _("Cannot assign value to inactive attribute.")
            )

        return value

    def validate_translations(self, value):
        """Validate that at least the default language translation exists."""
        if not value:
            raise serializers.ValidationError(
                "At least one translation is required."
            )

        # Check if default language (English) has a value
        if "en" in value and not value["en"].get("value"):
            raise serializers.ValidationError(
                "Default language (English) translation is required."
            )

        return value
