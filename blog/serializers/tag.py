from drf_spectacular.utils import extend_schema_field
from parler_rest.fields import TranslatedFieldsField
from parler_rest.serializers import TranslatableModelSerializer
from rest_framework import serializers

from blog.models.post import BlogPost
from blog.models.tag import BlogTag
from core.api.schema import generate_schema_multi_lang


@extend_schema_field(generate_schema_multi_lang(BlogPost))
class TranslatedFieldsFieldExtend(TranslatedFieldsField):
    pass


class BlogTagSerializer(
    TranslatableModelSerializer, serializers.ModelSerializer
):
    translations = TranslatedFieldsFieldExtend(shared_model=BlogTag)

    class Meta:
        model = BlogTag
        fields = (
            "translations",
            "id",
            "active",
            "sort_order",
            "created_at",
            "updated_at",
            "uuid",
            "get_posts_count",
        )
        read_only_fields = (
            "created_at",
            "updated_at",
            "uuid",
            "get_posts_count",
        )
