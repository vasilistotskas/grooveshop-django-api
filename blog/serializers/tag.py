from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from parler_rest.serializers import TranslatableModelSerializer
from rest_framework import serializers

from blog.models.tag import BlogTag
from core.api.schema import generate_schema_multi_lang
from core.utils.serializers import TranslatedFieldExtended


@extend_schema_field(generate_schema_multi_lang(BlogTag))
class TranslatedFieldsFieldExtend(TranslatedFieldExtended):
    pass


class BlogTagSerializer(
    TranslatableModelSerializer, serializers.ModelSerializer[BlogTag]
):
    posts_count = serializers.CharField(
        source="get_posts_count",
        read_only=True,
        help_text=_("Number of blog posts using this tag"),
    )
    translations = TranslatedFieldsFieldExtend(shared_model=BlogTag)

    class Meta:
        model = BlogTag
        fields = (
            "id",
            "translations",
            "active",
            "sort_order",
            "posts_count",
            "created_at",
            "updated_at",
            "uuid",
        )
        read_only_fields = (
            "id",
            "posts_count",
            "created_at",
            "updated_at",
            "uuid",
        )


class BlogTagDetailSerializer(BlogTagSerializer):
    class Meta(BlogTagSerializer.Meta):
        fields = (*BlogTagSerializer.Meta.fields,)


class BlogTagWriteSerializer(
    TranslatableModelSerializer, serializers.ModelSerializer[BlogTag]
):
    translations = TranslatedFieldsFieldExtend(shared_model=BlogTag)

    def validate_sort_order(self, value: int) -> int:
        if value is not None and value < 0:
            raise serializers.ValidationError(
                _("Sort order cannot be negative.")
            )
        return value

    class Meta:
        model = BlogTag
        fields = (
            "translations",
            "active",
            "sort_order",
        )
