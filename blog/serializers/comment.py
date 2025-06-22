from datetime import timedelta
from typing import Any

from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from drf_spectacular.helpers import lazy_serializer
from drf_spectacular.utils import extend_schema_field
from mptt.fields import TreeForeignKey
from parler_rest.serializers import TranslatableModelSerializer
from rest_framework import serializers
from rest_framework.relations import PrimaryKeyRelatedField

from authentication.serializers import AuthenticationSerializer
from blog.models.comment import BlogComment
from blog.models.post import BlogPost
from core.api.schema import generate_schema_multi_lang
from core.utils.serializers import TranslatedFieldExtended

User = get_user_model()


@extend_schema_field(generate_schema_multi_lang(BlogComment))
class TranslatedFieldsFieldExtend(TranslatedFieldExtended):
    pass


class BlogCommentSerializer(
    TranslatableModelSerializer, serializers.ModelSerializer[BlogComment]
):
    user = AuthenticationSerializer(read_only=True)
    content_preview = serializers.SerializerMethodField(
        help_text=_("First 150 characters of the comment content")
    )
    is_reply = serializers.SerializerMethodField(
        help_text=_("Whether this comment is a reply to another comment")
    )
    has_replies = serializers.SerializerMethodField(
        help_text=_("Whether this comment has replies")
    )
    is_edited = serializers.SerializerMethodField(
        help_text=_("Whether this comment has been edited")
    )
    user_has_liked = serializers.SerializerMethodField(
        help_text=_("Whether the current user has liked this comment")
    )
    translations = TranslatedFieldsFieldExtend(shared_model=BlogComment)

    def get_content_preview(
        self, obj: BlogComment | TreeForeignKey
    ) -> str | None:
        content = obj.safe_translation_getter("content", any_language=True)
        if content:
            return content[:150] + "..." if len(content) > 150 else content
        return None

    def get_is_reply(self, obj: BlogComment) -> bool:
        return obj.parent is not None

    def get_has_replies(self, obj: BlogComment) -> bool:
        return obj.get_children().exists()

    def get_is_edited(self, obj: BlogComment) -> bool:
        return obj.updated_at > obj.created_at + timedelta(minutes=5)

    def get_user_has_liked(self, obj: BlogComment) -> bool:
        request = self.context.get("request")
        if request and request.user.is_authenticated:
            return obj.likes.filter(id=request.user.id).exists()
        return False

    class Meta:
        model = BlogComment
        fields = (
            "id",
            "translations",
            "user",
            "content_preview",
            "is_reply",
            "parent",
            "has_replies",
            "is_approved",
            "is_edited",
            "likes_count",
            "replies_count",
            "user_has_liked",
            "created_at",
            "updated_at",
            "uuid",
        )
        read_only_fields = (
            "id",
            "user",
            "content_preview",
            "is_reply",
            "parent",
            "has_replies",
            "is_approved",
            "is_edited",
            "likes_count",
            "replies_count",
            "user_has_liked",
            "created_at",
            "updated_at",
            "uuid",
        )


class BlogCommentDetailSerializer(BlogCommentSerializer):
    post = serializers.SerializerMethodField(
        help_text=_("Basic information about the blog post")
    )
    parent_comment = serializers.SerializerMethodField(
        help_text=_("Parent comment if this is a reply")
    )
    children_comments = serializers.SerializerMethodField(
        help_text=_("Direct child comments (replies)")
    )
    ancestors_path = serializers.SerializerMethodField(
        help_text=_("Path from root comment to this comment")
    )
    tree_position = serializers.SerializerMethodField(
        help_text=_("Position information in the comment tree")
    )

    @extend_schema_field(
        lazy_serializer("blog.serializers.post.BlogPostSerializer")()
    )
    def get_post(self, obj: BlogComment):
        from blog.serializers.post import BlogPostSerializer  # noqa: PLC0415, I001

        return BlogPostSerializer(obj.post, context=self.context).data

    @extend_schema_field(
        {
            "type": "object",
            "nullable": True,
            "properties": {
                "id": {"type": "integer"},
                "content_preview": {"type": "string"},
                "user": {"$ref": "#/components/schemas/Authentication"},
                "created_at": {"type": "string", "format": "date-time"},
            },
            "required": ["id", "content_preview", "user", "created_at"],
        }
    )
    def get_parent_comment(self, obj: BlogComment):
        if obj.parent:
            return {
                "id": obj.parent.id,
                "content_preview": self.get_content_preview(obj.parent),
                "user": AuthenticationSerializer(obj.parent.user).data,
                "created_at": obj.parent.created_at,
            }
        return None

    @extend_schema_field(
        lazy_serializer("blog.serializers.comment.BlogCommentSerializer")(
            many=True
        )
    )
    def get_children_comments(self, obj: BlogComment):
        children = (
            obj.get_children().select_related("user").order_by("created_at")
        )
        if children.exists():
            return BlogCommentSerializer(
                children, many=True, context=self.context
            ).data
        return []

    @extend_schema_field(
        {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "content_preview": {"type": "string"},
                    "user": {"$ref": "#/components/schemas/Authentication"},
                },
                "required": ["id", "content_preview", "user"],
            },
        }
    )
    def get_ancestors_path(self, obj: BlogComment):
        ancestors = obj.get_ancestors().select_related("user")
        return [
            {
                "id": ancestor.id,
                "content_preview": self.get_content_preview(ancestor),
                "user": AuthenticationSerializer(ancestor.user).data,
            }
            for ancestor in ancestors
        ]

    @extend_schema_field(
        {
            "type": "object",
            "properties": {
                "level": {"type": "integer"},
                "tree_id": {"type": "integer"},
                "position_in_tree": {"type": "integer"},
                "siblings_count": {"type": "integer"},
            },
            "required": [
                "level",
                "tree_id",
                "position_in_tree",
                "siblings_count",
            ],
        }
    )
    def get_tree_position(self, obj: BlogComment):
        return {
            "level": obj.level,
            "tree_id": obj.tree_id,
            "position_in_tree": obj.get_descendant_count(),
            "siblings_count": obj.get_siblings().count(),
        }

    class Meta(BlogCommentSerializer.Meta):
        fields = (
            *BlogCommentSerializer.Meta.fields,
            "post",
            "parent_comment",
            "children_comments",
            "ancestors_path",
            "tree_position",
        )


class BlogCommentWriteSerializer(
    TranslatableModelSerializer, serializers.ModelSerializer[BlogComment]
):
    user = PrimaryKeyRelatedField(queryset=User.objects.all(), required=False)
    post = PrimaryKeyRelatedField(queryset=BlogPost.objects.all())
    parent = PrimaryKeyRelatedField(
        queryset=BlogComment.objects.all(), required=False, allow_null=True
    )
    translations = TranslatedFieldsFieldExtend(shared_model=BlogComment)

    def validate_parent(self, value: BlogComment) -> BlogComment:
        if value:
            post = self.initial_data.get("post")
            if isinstance(post, int):
                if value.post.id != post:
                    raise serializers.ValidationError(
                        _("Parent comment must belong to the same post.")
                    )
            elif post and value.post != post:
                raise serializers.ValidationError(
                    _("Parent comment must belong to the same post.")
                )
        return value

    def create(self, validated_data: Any) -> BlogComment:
        if "user" not in validated_data:
            validated_data["user"] = self.context["request"].user
        return super().create(validated_data)

    class Meta:
        model = BlogComment
        fields = (
            "translations",
            "user",
            "post",
            "parent",
        )
        read_only_fields = ("user",)


class BlogCommentLikedCommentsRequestSerializer(serializers.Serializer):
    comment_ids = serializers.ListField(
        child=serializers.IntegerField(),
        help_text=_("List of comment IDs to check like status for"),
    )

    def validate_comment_ids(self, value: list) -> list:
        if not value:
            raise serializers.ValidationError(
                _("At least one comment ID is required.")
            )
        if len(value) > 100:
            raise serializers.ValidationError(
                _("Cannot check more than 100 comments at once.")
            )
        return value


class BlogCommentLikedCommentsResponseSerializer(serializers.Serializer):
    liked_comment_ids = serializers.ListField(
        child=serializers.IntegerField(),
        help_text=_("List of comment IDs that are liked by the current user"),
    )


class BlogCommentMyCommentRequestSerializer(serializers.Serializer):
    post = serializers.IntegerField(
        help_text=_("Blog post ID to find comment for")
    )
