from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from parler_rest.serializers import (
    TranslatableModelSerializer,
)
from rest_framework import serializers
from rest_framework.relations import PrimaryKeyRelatedField

from core.api.schema import generate_schema_multi_lang
from core.utils.serializers import TranslatedFieldExtended
from country.models import Country


@extend_schema_field(generate_schema_multi_lang(Country))
class TranslatedFieldsFieldExtend(TranslatedFieldExtended):
    pass


class CountrySerializer(
    TranslatableModelSerializer, serializers.ModelSerializer[Country]
):
    translations = TranslatedFieldsFieldExtend(shared_model=Country)

    class Meta:
        model = Country
        fields = (
            "translations",
            "alpha_2",
            "alpha_3",
            "iso_cc",
            "phone_code",
            "sort_order",
            "created_at",
            "updated_at",
            "uuid",
            "main_image_path",
        )
        read_only_fields = (
            "created_at",
            "updated_at",
            "uuid",
            "main_image_path",
        )


class CountryDetailSerializer(CountrySerializer):
    regions = PrimaryKeyRelatedField(many=True, read_only=True)

    class Meta(CountrySerializer.Meta):
        fields = (
            *CountrySerializer.Meta.fields,
            "regions",
        )


class CountryWriteSerializer(
    TranslatableModelSerializer, serializers.ModelSerializer[Country]
):
    translations = TranslatedFieldsFieldExtend(shared_model=Country)

    def validate_alpha_2(self, value):
        if len(value) != 2:
            raise serializers.ValidationError(
                _("Alpha-2 code must be exactly 2 characters.")
            )
        return value.upper()

    def validate_alpha_3(self, value):
        if len(value) != 3:
            raise serializers.ValidationError(
                _("Alpha-3 code must be exactly 3 characters.")
            )
        return value.upper()

    def validate_phone_code(self, value):
        if value is not None and (value <= 0 or value > 9999):
            raise serializers.ValidationError(
                _("Phone code must be between 1 and 9999.")
            )
        return value

    def validate_iso_cc(self, value):
        if value is not None and (value <= 0 or value > 999):
            raise serializers.ValidationError(
                _("ISO country code must be between 1 and 999.")
            )
        return value

    class Meta:
        model = Country
        fields = (
            "translations",
            "alpha_2",
            "alpha_3",
            "iso_cc",
            "phone_code",
            "sort_order",
        )
