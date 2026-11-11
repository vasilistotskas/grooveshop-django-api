from typing import cast

from django.conf import settings
from django.utils.text import format_lazy
from django.utils.translation import gettext_lazy as _
from drf_spectacular.helpers import lazy_serializer
from drf_spectacular.utils import extend_schema_field
from parler_rest.serializers import TranslatableModelSerializer
from parler_rest.utils import create_translated_fields_serializer
from rest_framework import serializers
from rest_framework.relations import PrimaryKeyRelatedField

from blog.models.author import BlogAuthor
from blog.models.category import BlogCategory
from blog.models.post import BlogPost
from blog.models.tag import BlogTag
from blog.utils import calculate_reading_time
from core.api.schema import generate_schema_multi_lang
from core.utils.serializers import TranslatedFieldExtended


@extend_schema_field(generate_schema_multi_lang(BlogPost))
class TranslatedFieldsFieldExtend(TranslatedFieldExtended):
    pass


# What a post card reads of a translation. The body (the whole article,
# which `content_preview` stands in for) and the SEO copy belong to the
# post page: a list of posts, and every list a post is embedded in (the
# author's recent and top posts, a comment's post), used to carry each
# article in full, 100 to 200 KB a page.
LIST_TRANSLATED_FIELDS = ("title", "subtitle")


@extend_schema_field(
    generate_schema_multi_lang(BlogPost, fields=LIST_TRANSLATED_FIELDS)
)
class ListTranslatedFieldsField(TranslatedFieldExtended):
    pass


class BlogPostSerializer(
    TranslatableModelSerializer, serializers.ModelSerializer[BlogPost]
):
    category = PrimaryKeyRelatedField(queryset=BlogCategory.objects.all())
    tags = PrimaryKeyRelatedField(queryset=BlogTag.objects.all(), many=True)
    author = PrimaryKeyRelatedField(queryset=BlogAuthor.objects.all())
    translations = ListTranslatedFieldsField(
        shared_model=BlogPost,
        serializer_class=create_translated_fields_serializer(
            BlogPost,
            related_name="translations",
            meta={"fields": list(LIST_TRANSLATED_FIELDS)},
        ),
    )

    reading_time = serializers.SerializerMethodField(required=False)
    content_preview = serializers.SerializerMethodField(required=False)

    class Meta:
        model = BlogPost
        fields = (
            "id",
            "uuid",
            "slug",
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
            return cast(int, obj.reading_time)
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


# What a syndicated item renders: the article in full beside its card.
FEED_TRANSLATED_FIELDS = ("title", "subtitle", "body")


@extend_schema_field(
    generate_schema_multi_lang(BlogPost, fields=FEED_TRANSLATED_FIELDS)
)
class FeedTranslatedFieldsField(TranslatedFieldExtended):
    pass


class BlogPostFeedSerializer(
    TranslatableModelSerializer, serializers.ModelSerializer[BlogPost]
):
    """One item of the RSS feed.

    The feed needs every article's body, so it has its own shape and
    its own endpoint rather than a request per post for the detail.
    """

    translations = FeedTranslatedFieldsField(
        shared_model=BlogPost,
        serializer_class=create_translated_fields_serializer(
            BlogPost,
            related_name="translations",
            meta={"fields": list(FEED_TRANSLATED_FIELDS)},
        ),
    )

    class Meta:
        model = BlogPost
        fields = (
            "id",
            "uuid",
            "slug",
            "translations",
            "category",
            "main_image_path",
            "published_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class _CountField(serializers.IntegerField):
    """An integer with a default for an omitted value, never a blank one.

    DRF reads ``?count=`` as an omitted value whenever the field has a
    default, so a typo in the query was answered with the default feed.
    """

    def get_value(self, dictionary):
        if dictionary.get(self.field_name) == "":
            return ""
        return super().get_value(dictionary)


class BlogPostFeedQuerySerializer(serializers.Serializer):
    count = _CountField(
        min_value=1,
        max_value=settings.BLOG_FEED_LIMIT,
        default=settings.BLOG_FEED_LIMIT,
        help_text=format_lazy(
            _("Most posts to return, from 1 to {max} (the default)."),
            max=settings.BLOG_FEED_LIMIT,
        ),
    )


class BlogPostDetailSerializer(BlogPostSerializer):
    translations = TranslatedFieldsFieldExtend(shared_model=BlogPost)
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
        )
        read_only_fields = (
            *BlogPostSerializer.Meta.read_only_fields,
            "likes",
        )

    @extend_schema_field(
        lazy_serializer("blog.serializers.author.BlogAuthorDetailSerializer")()
    )
    def get_author(self, obj: BlogPost):
        from blog.serializers.author import BlogAuthorDetailSerializer

        return BlogAuthorDetailSerializer(obj.author, context=self.context).data

    @extend_schema_field(
        lazy_serializer(
            "blog.serializers.category.BlogCategoryDetailSerializer"
        )()
    )
    def get_category(self, obj: BlogPost):
        from blog.serializers.category import BlogCategoryDetailSerializer

        return BlogCategoryDetailSerializer(
            obj.category, context=self.context
        ).data

    @extend_schema_field(
        lazy_serializer("blog.serializers.tag.BlogTagDetailSerializer")(
            many=True
        )
    )
    def get_tags(self, obj: BlogPost):
        from blog.serializers.tag import BlogTagDetailSerializer

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
        )

    def validate_slug(self, value: str) -> str:
        if not value:
            raise serializers.ValidationError(
                _("Slug is required."), code="slug_required"
            )

        queryset = BlogPost.objects.filter(slug=value)
        if self.instance:
            queryset = queryset.exclude(pk=self.instance.pk)

        if queryset.exists():
            raise serializers.ValidationError(
                _("A post with this slug already exists."), code="slug_taken"
            )

        return value

    def validate_tags(self, value: list) -> list:
        if not value:
            raise serializers.ValidationError(
                _("At least one tag is required."), code="tags_required"
            )

        if len(value) > 10:
            raise serializers.ValidationError(
                _("Maximum 10 tags allowed per post."), code="too_many_tags"
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
