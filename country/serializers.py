from drf_spectacular.utils import extend_schema_field
from parler_rest.serializers import TranslatableModelSerializer
from parler_rest.serializers import TranslatedFieldsField

from core.api.schema import generate_schema_multi_lang
from core.api.serializers import BaseExpandSerializer
from country.models import Country


@extend_schema_field(generate_schema_multi_lang(Country))
class TranslatedFieldsFieldExtend(TranslatedFieldsField):
    pass


class CountrySerializer(TranslatableModelSerializer, BaseExpandSerializer):
    translations = TranslatedFieldsFieldExtend(shared_model=Country)

    class Meta:
        model = Country
        fields = (
            "translations",
            "alpha_2",
            "alpha_3",
            "iso_cc",
            "phone_code",
            "created_at",
            "updated_at",
            "sort_order",
            "uuid",
            "main_image_absolute_url",
            "main_image_filename",
        )
