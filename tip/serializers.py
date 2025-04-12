from drf_spectacular.utils import extend_schema_field
from parler_rest.fields import TranslatedFieldsField
from parler_rest.serializers import TranslatableModelSerializer
from rest_framework import serializers

from core.api.schema import generate_schema_multi_lang
from tip.models import Tip


@extend_schema_field(generate_schema_multi_lang(Tip))
class TranslatedFieldsFieldExtend(TranslatedFieldsField):
    pass


class TipSerializer(TranslatableModelSerializer, serializers.ModelSerializer):
    translations = TranslatedFieldsFieldExtend(shared_model=Tip)

    class Meta:
        model = Tip
        fields = (
            "translations",
            "kind",
            "icon",
            "active",
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
