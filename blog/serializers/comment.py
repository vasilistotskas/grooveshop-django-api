from django.contrib.auth import get_user_model
from drf_spectacular.utils import extend_schema_field
from parler_rest.fields import TranslatedFieldsField
from parler_rest.serializers import TranslatableModelSerializer
from rest_framework import serializers
from rest_framework.relations import PrimaryKeyRelatedField

from blog.models.comment import BlogComment
from blog.models.post import BlogPost
from core.api.schema import generate_schema_multi_lang

User = get_user_model()


@extend_schema_field(generate_schema_multi_lang(BlogComment))
class TranslatedFieldsFieldExtend(TranslatedFieldsField):
    pass


class BlogCommentSerializer(
    TranslatableModelSerializer, serializers.ModelSerializer
):
    children = serializers.SerializerMethodField()
    user = PrimaryKeyRelatedField(queryset=User.objects.all())
    post = PrimaryKeyRelatedField(queryset=BlogPost.objects.all())
    likes = PrimaryKeyRelatedField(
        queryset=User.objects.all(), many=True, required=False
    )
    translations = TranslatedFieldsFieldExtend(shared_model=BlogComment)

    def get_children(self, obj: BlogComment) -> list:
        if obj.get_children().exists():
            return list(obj.get_children().values_list("id", flat=True))
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
            "likes_count",
            "replies_count",
        )
        read_only_fields = (
            "created_at",
            "updated_at",
            "uuid",
            "likes_count",
            "replies_count",
        )
