from typing import Dict
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
from blog.serializers.author import BlogAuthorSerializer
from blog.serializers.category import BlogCategorySerializer
from blog.serializers.tag import BlogTagSerializer
from core.api.schema import generate_schema_multi_lang
from core.api.serializers import BaseExpandSerializer
from user.serializers.account import UserAccountSerializer

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
            "created_at",
            "updated_at",
            "published_at",
            "is_published",
            "uuid",
            "main_image_absolute_url",
            "main_image_filename",
            "number_of_likes",
            "number_of_comments",
            "post_tags_count",
        )

    def get_expand_fields(self) -> Dict[str, Type[serializers.ModelSerializer]]:
        return {
            "likes": UserAccountSerializer,
            "category": BlogCategorySerializer,
            "tags": BlogTagSerializer,
            "author": BlogAuthorSerializer,
        }
