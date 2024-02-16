import importlib
from typing import Dict
from typing import Type

from django.contrib.auth import get_user_model
from drf_spectacular.utils import extend_schema_field
from parler_rest.fields import TranslatedFieldsField
from parler_rest.serializers import TranslatableModelSerializer
from rest_framework import serializers
from rest_framework.relations import PrimaryKeyRelatedField
from rest_framework.utils.serializer_helpers import ReturnDict

from blog.models.comment import BlogComment
from blog.models.post import BlogPost
from core.api.schema import generate_schema_multi_lang
from core.api.serializers import BaseExpandSerializer

User = get_user_model()


@extend_schema_field(generate_schema_multi_lang(BlogComment))
class TranslatedFieldsFieldExtend(TranslatedFieldsField):
    pass


class BlogCommentSerializer(TranslatableModelSerializer, BaseExpandSerializer):
    children = serializers.SerializerMethodField()
    user = PrimaryKeyRelatedField(queryset=User.objects.all())
    post = PrimaryKeyRelatedField(queryset=BlogPost.objects.all())
    likes = PrimaryKeyRelatedField(queryset=User.objects.all(), many=True)
    translations = TranslatedFieldsFieldExtend(shared_model=BlogComment)

    def get_children(self, obj: BlogComment) -> ReturnDict | list:
        if obj.get_children().exists():
            return BlogCommentSerializer(
                obj.get_children(), many=True, context=self.context
            ).data
        return []

    class Meta:
        model = BlogComment
        fields = (
            "translations",
            "id",
            "is_approved",
            "likes",
            "user",
            "post",
            "children",
            "parent",
            "level",
            "tree_id",
            "created_at",
            "updated_at",
            "uuid",
            "number_of_likes",
        )

    def get_expand_fields(self) -> Dict[str, Type[serializers.ModelSerializer]]:
        user_account_serializer = importlib.import_module(
            "user.serializers.account"
        ).UserAccountSerializer
        blog_post_serializer = importlib.import_module(
            "blog.serializers.post"
        ).BlogPostSerializer
        return {
            "user": user_account_serializer,
            "post": blog_post_serializer,
            "likes": user_account_serializer,
        }
