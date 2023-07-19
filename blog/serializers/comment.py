from typing import Dict
from typing import Type

from drf_spectacular.utils import extend_schema_field
from parler_rest.fields import TranslatedFieldsField
from parler_rest.serializers import TranslatableModelSerializer
from rest_framework import serializers
from rest_framework.relations import PrimaryKeyRelatedField

from blog.models.comment import BlogComment
from blog.models.post import BlogPost
from blog.serializers.post import BlogPostSerializer
from core.api.schema import generate_schema_multi_lang
from core.api.serializers import BaseExpandSerializer
from user.models import UserAccount
from user.serializers.account import UserAccountSerializer


@extend_schema_field(generate_schema_multi_lang(BlogComment))
class TranslatedFieldsFieldExtend(TranslatedFieldsField):
    pass


class BlogCommentSerializer(TranslatableModelSerializer, BaseExpandSerializer):
    user = PrimaryKeyRelatedField(queryset=UserAccount.objects.all())
    post = PrimaryKeyRelatedField(queryset=BlogPost.objects.all())
    likes = PrimaryKeyRelatedField(queryset=UserAccount.objects.all(), many=True)
    translations = TranslatedFieldsFieldExtend(shared_model=BlogComment)

    class Meta:
        model = BlogComment
        fields = (
            "translations",
            "id",
            "is_approved",
            "likes",
            "user",
            "post",
            "created_at",
            "updated_at",
            "uuid",
            "number_of_likes",
        )

    def get_expand_fields(self) -> Dict[str, Type[serializers.ModelSerializer]]:
        return {
            "user": UserAccountSerializer,
            "post": BlogPostSerializer,
            "likes": UserAccountSerializer,
        }
