from typing import Dict
from typing import Type

from rest_framework import serializers
from rest_framework.relations import PrimaryKeyRelatedField

from blog.models.author import BlogAuthor
from core.api.serializers import BaseExpandSerializer
from user.models import UserAccount
from user.serializers.account import UserAccountSerializer


class BlogAuthorSerializer(BaseExpandSerializer):
    user = PrimaryKeyRelatedField(queryset=UserAccount.objects.all())

    class Meta:
        model = BlogAuthor
        fields = (
            "id",
            "user",
            "website",
            "bio",
            "created_at",
            "updated_at",
            "uuid",
        )

    def get_expand_fields(self) -> Dict[str, Type[serializers.ModelSerializer]]:
        return {
            "user": UserAccountSerializer,
        }
