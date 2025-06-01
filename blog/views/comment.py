from __future__ import annotations

from django.conf import settings
from django.utils.translation import gettext_lazy as _
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import (
    extend_schema,
    extend_schema_view,
    inline_serializer,
)
from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.permissions import (
    IsAuthenticated,
)
from rest_framework.response import Response

from blog.filters.comment import BlogCommentFilter
from blog.models.comment import BlogComment
from blog.serializers.comment import BlogCommentSerializer
from blog.serializers.post import BlogPostSerializer
from core.api.serializers import ErrorResponseSerializer
from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.serializers import MultiSerializerMixin
from core.utils.views import cache_methods


@extend_schema_view(
    list=extend_schema(
        summary=_("List blog comments"),
        description=_(
            "Retrieve a list of blog comments with hierarchical support. "
            "Supports filtering by post, user, approval status, and content. "
        ),
        tags=["Blog Comments"],
        responses={
            200: BlogCommentSerializer(many=True),
        },
    ),
    retrieve=extend_schema(
        summary=_("Retrieve a blog comment"),
        description=_(
            "Get detailed information about a specific blog comment including replies."
        ),
        tags=["Blog Comments"],
        responses={
            200: BlogCommentSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    create=extend_schema(
        summary=_("Create a blog comment"),
        description=_(
            "Create a new blog comment. Requires authentication. "
            "Comments are subject to approval before being visible."
        ),
        tags=["Blog Comments"],
        responses={
            201: BlogCommentSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
        },
    ),
    update=extend_schema(
        summary=_("Update a blog comment"),
        description=_("Update your own blog comment. Requires authentication."),
        tags=["Blog Comments"],
        responses={
            200: BlogCommentSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    partial_update=extend_schema(
        summary=_("Partially update a blog comment"),
        description=_(
            "Partially update your own blog comment. Requires authentication."
        ),
        tags=["Blog Comments"],
        responses={
            200: BlogCommentSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    destroy=extend_schema(
        summary=_("Delete a blog comment"),
        description=_("Delete your own blog comment. Requires authentication."),
        tags=["Blog Comments"],
        responses={
            204: None,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    replies=extend_schema(
        summary=_("Get comment replies"),
        description=_(
            "Get all replies (children) of this comment in threaded structure."
        ),
        tags=["Blog Comments"],
        responses={
            200: BlogCommentSerializer(many=True),
            404: ErrorResponseSerializer,
        },
    ),
    thread=extend_schema(
        summary=_("Get comment thread"),
        description=_(
            "Get the complete thread (all ancestors and descendants) of this comment."
        ),
        tags=["Blog Comments"],
        responses={
            200: BlogCommentSerializer(many=True),
        },
    ),
    update_likes=extend_schema(
        summary=_("Toggle comment like"),
        description=_("Like or unlike a comment. Toggles the like status."),
        tags=["Blog Comments"],
        responses={
            200: BlogCommentSerializer,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    liked_comments=extend_schema(
        summary=_("Check bulk like status"),
        description=_(
            "Check which comments from a list are liked by the current user."
        ),
        tags=["Blog Comments"],
        request=inline_serializer(
            name="BlogPostLikedCommentsRequest",
            fields={
                "comment_ids": serializers.ListField(
                    child=serializers.IntegerField()
                )
            },
        ),
        responses={
            200: inline_serializer(
                name="LikedCommentsResponse",
                fields={
                    "liked_comment_ids": serializers.ListField(
                        child=serializers.IntegerField()
                    )
                },
            ),
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
        },
    ),
    post=extend_schema(
        summary=_("Get comment's blog post"),
        description=_("Get the blog post that this comment belongs to."),
        tags=["Blog Comments"],
        responses={
            200: BlogPostSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    my_comments=extend_schema(
        summary=_("Get current user's comments"),
        description=_(
            "Get all comments made by the currently authenticated user."
        ),
        tags=["Blog Comments"],
        responses={
            200: BlogCommentSerializer(many=True),
            401: ErrorResponseSerializer,
        },
    ),
    my_comment=extend_schema(
        summary=_("Get current user's comment"),
        description=_(
            "Get the comment made by the currently authenticated user for a specific post."
        ),
        tags=["Blog Comments"],
        request=inline_serializer(
            name="MyCommentRequest",
            fields={
                "post": serializers.IntegerField(),
            },
        ),
        responses={
            200: BlogCommentSerializer,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
)
@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class BlogCommentViewSet(MultiSerializerMixin, BaseModelViewSet):
    filterset_class = BlogCommentFilter
    filter_backends = [
        DjangoFilterBackend,
        PascalSnakeCaseOrderingFilter,
        SearchFilter,
    ]
    ordering_fields = [
        "id",
        "created_at",
        "updated_at",
        "level",
        "lft",
        "likes_count",
    ]
    ordering = ["-created_at"]
    search_fields = [
        "translations__content",
        "user__first_name",
        "user__last_name",
        "user__email",
    ]

    serializers = {
        "default": BlogCommentSerializer,
        "post": BlogPostSerializer,
    }

    def get_queryset(self):
        queryset = BlogComment.objects.select_related(
            "user", "post", "parent"
        ).prefetch_related("translations", "likes", "children")

        return queryset

    def get_permissions(self):
        if self.action in [
            "create",
            "update",
            "partial_update",
            "destroy",
            "update_likes",
        ] or self.action in ["moderate", "bulk_moderate"]:
            self.permission_classes = [IsAuthenticated]
        return super().get_permissions()

    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    @action(detail=True, methods=["GET"])
    def replies(self, request, pk=None):
        comment = self.get_object()

        if not comment.get_children().exists():
            return Response(
                {"detail": _("No replies found")},
                status=status.HTTP_404_NOT_FOUND,
            )

        queryset = (
            comment.get_children()
            .select_related("user", "parent")
            .prefetch_related("translations", "likes")
        )

        return self.paginate_and_serialize(queryset, request)

    @action(detail=True, methods=["GET"])
    def thread(self, request, pk=None):
        comment = self.get_object()

        root = comment.get_root()

        queryset = (
            root.get_descendants(include_self=True)
            .select_related("user", "parent")
            .prefetch_related("translations", "likes")
        )

        return self.paginate_and_serialize(queryset, request)

    @action(detail=True, methods=["POST"])
    def update_likes(self, request, pk=None):
        comment = self.get_object()
        user = request.user

        if comment.likes.filter(id=user.id).exists():
            comment.likes.remove(user)
            action_taken = "unliked"
        else:
            comment.likes.add(user)
            action_taken = "liked"

        serializer = self.get_serializer(
            comment, context=self.get_serializer_context()
        )

        response_data = serializer.data
        response_data["action"] = action_taken

        return Response(response_data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["GET"])
    def post(self, request, pk=None):
        comment = self.get_object()
        if not comment.post:
            return Response(
                {"detail": _("Comment has no associated post.")},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = BlogPostSerializer(
            comment.post, context=self.get_serializer_context()
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["POST"])
    def liked_comments(self, request, *args, **kwargs):
        user = request.user
        comment_ids = request.data.get("comment_ids", [])

        if not comment_ids:
            return Response(
                {"error": _("No comment IDs provided.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        liked_comment_ids = list(
            BlogComment.objects.filter(
                likes=user, id__in=comment_ids
            ).values_list("id", flat=True)
        )

        return Response(liked_comment_ids, status=status.HTTP_200_OK)

    @action(detail=False, methods=["GET"])
    def my_comments(self, request):
        queryset = self.get_queryset().filter(user=request.user)
        return self.paginate_and_serialize(queryset, request)

    @action(detail=False, methods=["POST"])
    def my_comment(self, request, *args, **kwargs) -> Response:
        if not request.user.is_authenticated:
            return Response(
                {"detail": _("User is not authenticated")},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        post_id = request.data.get("post")
        user_id = request.user.id

        if not user_id or not post_id:
            return Response(
                {"detail": _("User and Post are required fields")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            comment = BlogComment.objects.get(
                user=user_id, post=post_id, parent=None
            )
            serializer = self.get_serializer(comment)
            return Response(serializer.data, status=status.HTTP_200_OK)

        except BlogComment.DoesNotExist:
            return Response(
                {"detail": _("Comment does not exist")},
                status=status.HTTP_404_NOT_FOUND,
            )

        except ValueError:
            return Response(
                {"detail": _("Invalid data")},
                status=status.HTTP_400_BAD_REQUEST,
            )
