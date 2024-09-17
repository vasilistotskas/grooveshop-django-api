import importlib
from typing import Dict
from typing import override
from typing import Type

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
from core.api.serializers import BaseExpandSerializer

User = get_user_model()


@extend_schema_field(generate_schema_multi_lang(BlogPost))
class TranslatedFieldsFieldExtend(TranslatedFieldsField):
    pass


class BlogPostSerializer(TranslatableModelSerializer, BaseExpandSerializer):
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
            "status",
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

    @override
    def get_expand_fields(
        self,
    ) -> Dict[str, Type[serializers.ModelSerializer]]:
        user_account_serializer = importlib.import_module("authentication.serializers").AuthenticationSerializer
        blog_category_serializer = importlib.import_module("blog.serializers.category").BlogCategorySerializer
        blog_tag_serializer = importlib.import_module("blog.serializers.tag").BlogTagSerializer
        blog_author_serializer = importlib.import_module("blog.serializers.author").BlogAuthorSerializer
        return {
            "likes": user_account_serializer,
            "category": blog_category_serializer,
            "tags": blog_tag_serializer,
            "author": blog_author_serializer,
        }
