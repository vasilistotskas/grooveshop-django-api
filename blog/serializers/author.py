from django.contrib.auth import get_user_model
from django.db import models
from django.utils.translation import gettext_lazy as _
from drf_spectacular.helpers import lazy_serializer
from drf_spectacular.utils import extend_schema_field
from parler_rest.serializers import TranslatableModelSerializer
from rest_framework import serializers
from rest_framework.relations import PrimaryKeyRelatedField

from authentication.serializers import AuthenticationSerializer
from blog.models.author import BlogAuthor
from core.api.schema import generate_schema_multi_lang
from core.utils.serializers import TranslatedFieldExtended

User = get_user_model()


@extend_schema_field(generate_schema_multi_lang(BlogAuthor))
class TranslatedFieldsFieldExtend(TranslatedFieldExtended):
    pass


class BlogAuthorSerializer(
    TranslatableModelSerializer,
    serializers.ModelSerializer[BlogAuthor],
):
    user = PrimaryKeyRelatedField(queryset=User.objects.all())
    translations = TranslatedFieldsFieldExtend(shared_model=BlogAuthor)

    class Meta:
        model = BlogAuthor
        fields = (
            "translations",
            "id",
            "uuid",
            "user",
            "website",
            "created_at",
            "updated_at",
            "number_of_posts",
            "total_likes_received",
        )
        read_only_fields = (
            "created_at",
            "updated_at",
            "uuid",
            "number_of_posts",
            "total_likes_received",
        )


class BlogAuthorDetailSerializer(BlogAuthorSerializer):
    user = AuthenticationSerializer(read_only=True)
    recent_posts = serializers.SerializerMethodField()
    top_posts = serializers.SerializerMethodField()

    class Meta(BlogAuthorSerializer.Meta):
        fields = (
            *BlogAuthorSerializer.Meta.fields,
            "recent_posts",
            "top_posts",
        )

    @extend_schema_field(
        lazy_serializer("blog.serializers.post.BlogPostSerializer")(many=True)
    )
    def get_recent_posts(self, obj: BlogAuthor):
        from blog.serializers.post import BlogPostSerializer  # noqa: PLC0415, I001

        recent_posts = obj.blog_posts.order_by("-created_at")[:3]
        return BlogPostSerializer(
            recent_posts, many=True, context=self.context
        ).data

    @extend_schema_field(
        lazy_serializer("blog.serializers.post.BlogPostSerializer")(many=True)
    )
    def get_top_posts(self, obj: BlogAuthor):
        from blog.serializers.post import BlogPostSerializer  # noqa: PLC0415, I001

        top_posts = obj.blog_posts.annotate(
            likes_count_annotation=models.Count("likes")
        ).order_by("-view_count", "-likes_count_annotation")[:3]
        return BlogPostSerializer(
            top_posts, many=True, context=self.context
        ).data


class BlogAuthorWriteSerializer(
    TranslatableModelSerializer,
    serializers.ModelSerializer[BlogAuthor],
):
    user = PrimaryKeyRelatedField(queryset=User.objects.all())
    translations = TranslatedFieldsFieldExtend(shared_model=BlogAuthor)

    class Meta:
        model = BlogAuthor
        fields = (
            "translations",
            "user",
            "website",
        )

    def validate_user(self, value: User) -> User:
        if (
            self.instance is None
            and BlogAuthor.objects.filter(user=value).exists()
        ):
            raise serializers.ValidationError(
                _("This user already has an author profile.")
            )
        return value

    def validate_website(self, value: str) -> str:
        if value and not (
            value.startswith("http://") or value.startswith("https://")
        ):
            raise serializers.ValidationError(
                _("Website must start with http:// or https://")
            )
        return value
