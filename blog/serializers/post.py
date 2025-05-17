from django.contrib.auth import get_user_model
from drf_spectacular.utils import extend_schema_field
from parler_rest.fields import TranslatedFieldsField
from parler_rest.serializers import TranslatableModelSerializer
from rest_framework import serializers
from rest_framework.relations import PrimaryKeyRelatedField

from blog.models.author import BlogAuthor
from blog.models.category import BlogCategory
from blog.models.post import BlogPost
from blog.models.tag import BlogTag
from core.api.schema import generate_schema_multi_lang

User = get_user_model()


@extend_schema_field(generate_schema_multi_lang(BlogPost))
class TranslatedFieldsFieldExtend(TranslatedFieldsField):
    pass


class BlogPostSerializer(
    TranslatableModelSerializer, serializers.ModelSerializer
):
    likes = PrimaryKeyRelatedField(queryset=User.objects.all(), many=True)
    category = PrimaryKeyRelatedField(queryset=BlogCategory.objects.all())
    tags = PrimaryKeyRelatedField(queryset=BlogTag.objects.all(), many=True)
    author = PrimaryKeyRelatedField(queryset=BlogAuthor.objects.all())
    translations = TranslatedFieldsFieldExtend(shared_model=BlogPost)

    class Meta:
        model = BlogPost
        fields = (
            "translations",
            "id",
            "slug",
            "likes",
            "category",
            "tags",
            "author",
            "featured",
            "view_count",
            "is_published",
            "created_at",
            "updated_at",
            "published_at",
            "is_visible",
            "uuid",
            "main_image_path",
            "likes_count",
            "comments_count",
            "tags_count",
            "absolute_url",
            "seo_title",
            "seo_description",
            "seo_keywords",
        )
        read_only_fields = (
            "created_at",
            "updated_at",
            "published_at",
            "is_visible",
            "uuid",
            "main_image_path",
            "likes_count",
            "comments_count",
            "tags_count",
            "absolute_url",
        )
