import importlib
from typing import override

from drf_spectacular.utils import extend_schema_field
from parler_rest.fields import TranslatedFieldsField
from parler_rest.serializers import TranslatableModelSerializer
from rest_framework.relations import PrimaryKeyRelatedField

from core.api.schema import generate_schema_multi_lang
from core.api.serializers import BaseExpandSerializer
from country.models import Country
from region.models import Region


@extend_schema_field(generate_schema_multi_lang(Region))
class TranslatedFieldsFieldExtend(TranslatedFieldsField):
    pass


class RegionSerializer(TranslatableModelSerializer, BaseExpandSerializer):
    translations = TranslatedFieldsFieldExtend(shared_model=Region)
    country = PrimaryKeyRelatedField(queryset=Country.objects.all())

    class Meta:
        model = Region
        fields = (
            "translations",
            "alpha",
            "country",
            "sort_order",
            "created_at",
            "updated_at",
            "uuid",
        )
        read_only_fields = (
            "created_at",
            "updated_at",
            "uuid",
        )

    @override
    def get_expand_fields(
        self,
    ):
        country_serializer = importlib.import_module(
            "country.serializers"
        ).CountrySerializer
        return {
            "country": country_serializer,
        }
