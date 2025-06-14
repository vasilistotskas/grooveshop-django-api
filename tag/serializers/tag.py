from drf_spectacular.utils import extend_schema_field
from parler_rest.serializers import (
    TranslatableModelSerializer,
)

from core.api.schema import generate_schema_multi_lang
from core.utils.serializers import TranslatedFieldExtended
from tag.models import Tag


@extend_schema_field(generate_schema_multi_lang(Tag))
class TranslatedFieldsFieldExtend(TranslatedFieldExtended):
    pass


class TagSerializer(TranslatableModelSerializer):
    translations = TranslatedFieldsFieldExtend(shared_model=Tag)

    class Meta:
        model = Tag
        fields = [
            "id",
            "translations",
            "active",
            "sort_order",
            "created_at",
            "updated_at",
            "uuid",
        ]
        read_only_fields = (
            "created_at",
            "updated_at",
            "uuid",
        )
