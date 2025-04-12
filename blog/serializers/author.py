from django.contrib.auth import get_user_model
from drf_spectacular.utils import extend_schema_field
from parler_rest.fields import TranslatedFieldsField
from parler_rest.serializers import TranslatableModelSerializer
from rest_framework import serializers
from rest_framework.relations import PrimaryKeyRelatedField

from blog.models.author import BlogAuthor
from core.api.schema import generate_schema_multi_lang

User = get_user_model()


@extend_schema_field(generate_schema_multi_lang(BlogAuthor))
class TranslatedFieldsFieldExtend(TranslatedFieldsField):
    pass


class BlogAuthorSerializer(
    TranslatableModelSerializer, serializers.ModelSerializer
):
    user = PrimaryKeyRelatedField(queryset=User.objects.all())
    translations = TranslatedFieldsFieldExtend(shared_model=BlogAuthor)

    class Meta:
        model = BlogAuthor
        fields = (
            "translations",
            "id",
            "user",
            "website",
            "created_at",
            "updated_at",
            "uuid",
            "number_of_posts",
            "total_likes_received",
        )
        read_only_fields = (
            "created_at",
            "updated_at",
            "uuid",
            "full_name",
            "image",
            "number_of_posts",
            "total_likes_received",
        )
