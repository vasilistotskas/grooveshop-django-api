from typing import Dict
from typing import Type

from drf_spectacular.utils import extend_schema_field
from parler_rest.fields import TranslatedFieldsField
from parler_rest.serializers import TranslatableModelSerializer
from rest_framework import serializers
from rest_framework.relations import PrimaryKeyRelatedField

from blog.models.author import BlogAuthor
from core.api.schema import generate_schema_multi_lang
from core.api.serializers import BaseExpandSerializer
from user.models import UserAccount
from user.serializers.account import UserAccountSerializer


@extend_schema_field(generate_schema_multi_lang(BlogAuthor))
class TranslatedFieldsFieldExtend(TranslatedFieldsField):
    pass


class BlogAuthorSerializer(TranslatableModelSerializer, BaseExpandSerializer):
    user = PrimaryKeyRelatedField(queryset=UserAccount.objects.all())
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
        )

    def get_expand_fields(self) -> Dict[str, Type[serializers.ModelSerializer]]:
        return {
            "user": UserAccountSerializer,
        }
