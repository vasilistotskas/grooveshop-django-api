from drf_spectacular.utils import extend_schema_field
from parler_rest.serializers import (
    TranslatableModelSerializer,
    TranslatedFieldsField,
)
from rest_framework import serializers

from core.api.schema import generate_schema_multi_lang
from country.models import Country


@extend_schema_field(generate_schema_multi_lang(Country))
class TranslatedFieldsFieldExtend(TranslatedFieldsField):
    pass


class CountrySerializer(
    TranslatableModelSerializer, serializers.ModelSerializer
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
