from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from drf_spectacular.helpers import lazy_serializer
from drf_spectacular.utils import extend_schema_field
from parler_rest.serializers import TranslatableModelSerializer
from rest_framework import serializers
from rest_framework.relations import PrimaryKeyRelatedField

from blog.models.author import BlogAuthor
from blog.models.category import BlogCategory
from blog.models.post import BlogPost
from blog.models.tag import BlogTag
from blog.utils import calculate_reading_time
from core.api.schema import generate_schema_multi_lang
from core.utils.serializers import TranslatedFieldExtended

User = get_user_model()


@extend_schema_field(generate_schema_multi_lang(BlogPost))
class TranslatedFieldsFieldExtend(TranslatedFieldExtended):
    pass


class BlogPostSerializer(
    TranslatableModelSerializer, serializers.ModelSerializer[BlogPost]
):
    likes = PrimaryKeyRelatedField(queryset=User.objects.all(), many=True)
    category = PrimaryKeyRelatedField(queryset=BlogCategory.objects.all())
    tags = PrimaryKeyRelatedField(queryset=BlogTag.objects.all(), many=True)
    author = PrimaryKeyRelatedField(queryset=BlogAuthor.objects.all())
    translations = TranslatedFieldsFieldExtend(shared_model=BlogPost)

    reading_time = serializers.SerializerMethodField()
    content_preview = serializers.SerializerMethodField()

    class Meta:
        model = BlogPost
        fields = (
            "id",
            "uuid",
            "slug",
            "likes",
            "translations",
            "author",
            "category",
            "tags",
            "featured",
            "view_count",
            "likes_count",
            "comments_count",
            "tags_count",
            "is_published",
            "published_at",
            "created_at",
            "updated_at",
            "main_image_path",
            "reading_time",
            "content_preview",
        )
        read_only_fields = (
            "id",
            "uuid",
            "view_count",
            "likes_count",
            "comments_count",
            "tags_count",
            "published_at",
            "created_at",
            "updated_at",
            "main_image_path",
        )

    def get_reading_time(self, obj: BlogPost) -> int:
        if hasattr(obj, "reading_time"):
            return obj.reading_time
        body = obj.safe_translation_getter("body", any_language=True)
        if body:
            return calculate_reading_time(body)
        return 1

    def get_content_preview(self, obj: BlogPost) -> str:
        body = obj.safe_translation_getter("body", any_language=True)
        if body:
            preview = body[:200]
            return f"{preview}..." if len(body) > 200 else preview
        return ""


class BlogPostDetailSerializer(BlogPostSerializer):
    likes = PrimaryKeyRelatedField(many=True, read_only=True)
    author = serializers.SerializerMethodField()
    category = serializers.SerializerMethodField()
    tags = serializers.SerializerMethodField()
    user_has_liked = serializers.SerializerMethodField()

    class Meta(BlogPostSerializer.Meta):
        fields = (
            *BlogPostSerializer.Meta.fields,
            "likes",
            "user_has_liked",
            "seo_title",
            "seo_description",
            "seo_keywords",
        )
        read_only_fields = (
            *BlogPostSerializer.Meta.read_only_fields,
            "likes",
        )

    @extend_schema_field(
        lazy_serializer("blog.serializers.author.BlogAuthorDetailSerializer")()
    )
    def get_author(self, obj: BlogPost):
        from blog.serializers.author import BlogAuthorDetailSerializer  # noqa: PLC0415, I001

        return BlogAuthorDetailSerializer(obj.author, context=self.context).data

    @extend_schema_field(
        lazy_serializer(
            "blog.serializers.category.BlogCategoryDetailSerializer"
        )()
    )
    def get_category(self, obj: BlogPost):
        from blog.serializers.category import BlogCategoryDetailSerializer  # noqa: PLC0415, I001

        return BlogCategoryDetailSerializer(
            obj.category, context=self.context
        ).data

    @extend_schema_field(
        lazy_serializer("blog.serializers.tag.BlogTagDetailSerializer")(
            many=True
        )
    )
    def get_tags(self, obj: BlogPost):
        from blog.serializers.tag import BlogTagDetailSerializer  # noqa: PLC0415, I001

        return BlogTagDetailSerializer(
            obj.tags.all(), many=True, context=self.context
        ).data

    def get_user_has_liked(self, obj: BlogPost) -> bool:
        request = self.context.get("request")
        if request and request.user.is_authenticated:
            return obj.likes.filter(id=request.user.id).exists()
        return False


class BlogPostWriteSerializer(
    TranslatableModelSerializer, serializers.ModelSerializer[BlogPost]
):
    category = PrimaryKeyRelatedField(queryset=BlogCategory.objects.all())
    tags = PrimaryKeyRelatedField(
        queryset=BlogTag.objects.all(), many=True, required=False
    )
    author = PrimaryKeyRelatedField(queryset=BlogAuthor.objects.all())
    translations = TranslatedFieldsFieldExtend(shared_model=BlogPost)

    class Meta:
        model = BlogPost
        fields = (
            "translations",
            "slug",
            "category",
            "tags",
            "author",
            "featured",
            "is_published",
            "seo_title",
            "seo_description",
            "seo_keywords",
        )

    def validate_slug(self, value: str) -> str:
        if not value:
            raise serializers.ValidationError(_("Slug is required."))

        queryset = BlogPost.objects.filter(slug=value)
        if self.instance:
            queryset = queryset.exclude(pk=self.instance.pk)

        if queryset.exists():
            raise serializers.ValidationError(
                _("A post with this slug already exists.")
            )

        return value

    def validate_tags(self, value: list) -> list:
        if not value:
            raise serializers.ValidationError(
                _("At least one tag is required.")
            )

        if len(value) > 10:
            raise serializers.ValidationError(
                _("Maximum 10 tags allowed per post.")
            )

        return value


class BlogPostLikedPostsRequestSerializer(serializers.Serializer):
    post_ids = serializers.ListField(
        child=serializers.IntegerField(),
        help_text=_("List of post IDs to check for likes"),
    )


class BlogPostLikedPostsResponseSerializer(serializers.Serializer):
    post_ids = serializers.ListField(
        child=serializers.IntegerField(), help_text=_("List of liked post IDs")
    )
