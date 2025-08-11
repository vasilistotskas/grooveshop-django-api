from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from parler_rest.serializers import TranslatableModelSerializer
from rest_framework import serializers

from tag.models import Tag
from core.api.schema import generate_schema_multi_lang
from core.utils.serializers import TranslatedFieldExtended


@extend_schema_field(generate_schema_multi_lang(Tag))
class TranslatedFieldsFieldExtend(TranslatedFieldExtended):
    pass


class TagSerializer(
    TranslatableModelSerializer, serializers.ModelSerializer[Tag]
):
    usage_count = serializers.CharField(
        source="get_usage_count",
        read_only=True,
        help_text=_("Number of times this tag is used"),
    )
    translations = TranslatedFieldsFieldExtend(shared_model=Tag)

    class Meta:
        model = Tag
        fields = (
            "id",
            "translations",
            "active",
            "sort_order",
            "usage_count",
            "created_at",
            "updated_at",
            "uuid",
        )
        read_only_fields = (
            "id",
            "usage_count",
            "created_at",
            "updated_at",
            "uuid",
        )


class TagDetailSerializer(TagSerializer):
    content_types = serializers.CharField(
        source="get_content_types",
        read_only=True,
        help_text=_("Content types this tag is used with"),
    )

    class Meta(TagSerializer.Meta):
        fields = (*TagSerializer.Meta.fields, "content_types")


class TagWriteSerializer(
    TranslatableModelSerializer, serializers.ModelSerializer[Tag]
):
    translations = TranslatedFieldsFieldExtend(shared_model=Tag)

    def validate_sort_order(self, value: int) -> int:
        if value is not None and value < 0:
            raise serializers.ValidationError(
                _("Sort order cannot be negative.")
            )
        return value

    class Meta:
        model = Tag
        fields = (
            "translations",
            "active",
            "sort_order",
        )
