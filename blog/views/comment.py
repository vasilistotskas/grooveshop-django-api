from __future__ import annotations

from django.conf import settings
from django.utils.translation import gettext_lazy as _
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import (
    extend_schema,
    extend_schema_view,
)
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.permissions import (
    IsAuthenticated,
)
from rest_framework.response import Response

from blog.filters.comment import BlogCommentFilter
from blog.models.comment import BlogComment
from blog.models.post import BlogPost
from blog.serializers.comment import (
    BlogCommentDetailSerializer,
    BlogCommentLikedCommentsRequestSerializer,
    BlogCommentLikedCommentsResponseSerializer,
    BlogCommentListSerializer,
    BlogCommentMyCommentRequestSerializer,
    BlogCommentWriteSerializer,
)
from blog.serializers.post import (
    BlogPostDetailSerializer,
)
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
            200: BlogCommentListSerializer(many=True),
        },
    ),
    retrieve=extend_schema(
        summary=_("Retrieve a blog comment"),
        description=_(
            "Get detailed information about a specific blog comment including replies."
        ),
        tags=["Blog Comments"],
        responses={
            200: BlogCommentDetailSerializer,
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
        request=BlogCommentWriteSerializer,
        responses={
            201: BlogCommentDetailSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
        },
    ),
    update=extend_schema(
        summary=_("Update a blog comment"),
        description=_("Update your own blog comment. Requires authentication."),
        tags=["Blog Comments"],
        request=BlogCommentWriteSerializer,
        responses={
            200: BlogCommentDetailSerializer,
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
        request=BlogCommentWriteSerializer,
        responses={
            200: BlogCommentDetailSerializer,
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
            200: BlogCommentListSerializer(many=True),
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
            200: BlogCommentListSerializer(many=True),
        },
    ),
    update_likes=extend_schema(
        summary=_("Toggle comment like"),
        description=_("Like or unlike a comment. Toggles the like status."),
        tags=["Blog Comments"],
        responses={
            200: BlogCommentDetailSerializer,
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
        request=BlogCommentLikedCommentsRequestSerializer,
        responses={
            200: BlogCommentLikedCommentsResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
        },
    ),
    post=extend_schema(
        summary=_("Get comment's blog post"),
        description=_("Get the blog post that this comment belongs to."),
        tags=["Blog Comments"],
        responses={
            200: BlogPostDetailSerializer,
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
            200: BlogCommentListSerializer(many=True),
            401: ErrorResponseSerializer,
        },
    ),
    my_comment=extend_schema(
        summary=_("Get current user's comment"),
        description=_(
            "Get the comment made by the currently authenticated user for a specific post."
        ),
        tags=["Blog Comments"],
        request=BlogCommentMyCommentRequestSerializer,
        responses={
            200: BlogCommentDetailSerializer,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
)
@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class BlogCommentViewSet(MultiSerializerMixin, BaseModelViewSet):
    queryset = BlogComment.objects.select_related(
        "user", "post"
    ).prefetch_related("likes")
    filter_backends = [
        DjangoFilterBackend,
        SearchFilter,
        PascalSnakeCaseOrderingFilter,
    ]
    filterset_class = BlogCommentFilter
    search_fields = ["translations__content"]
    ordering_fields = [
        "created_at",
        "updated_at",
        "likes_count",
        "replies_count",
    ]
    ordering = ["-created_at"]
    serializers = {
        "list": BlogCommentListSerializer,
        "retrieve": BlogCommentDetailSerializer,
        "create": BlogCommentWriteSerializer,
        "update": BlogCommentWriteSerializer,
        "partial_update": BlogCommentWriteSerializer,
        "replies": BlogCommentListSerializer,
        "thread": BlogCommentListSerializer,
        "update_likes": BlogCommentDetailSerializer,
        "post": BlogPostDetailSerializer,
        "liked_comments": BlogCommentLikedCommentsResponseSerializer,
        "my_comments": BlogCommentListSerializer,
        "my_comment": BlogCommentDetailSerializer,
    }

    def get_queryset(self):
        return self.queryset.filter(is_approved=True)

    def get_permissions(self):
        permission_classes = []
        if self.action in [
            "create",
            "update",
            "partial_update",
            "destroy",
            "update_likes",
            "liked_comments",
            "my_comments",
            "my_comment",
        ]:
            permission_classes.append(IsAuthenticated)
        return [permission() for permission in permission_classes]

    @action(detail=True, methods=["GET"])
    def replies(self, request, pk=None):
        comment = self.get_object()
        queryset = (
            comment.get_children()
            .select_related("user", "post")
            .prefetch_related("likes")
            .filter(is_approved=True)
            .order_by("created_at")
        )
        return self.paginate_and_serialize(queryset, request)

    @action(detail=True, methods=["GET"])
    def thread(self, request, pk=None):
        comment = self.get_object()
        ancestors = comment.get_ancestors().select_related("user", "post")
        descendants = comment.get_descendants().select_related("user", "post")

        queryset = [*list(ancestors), comment, *list(descendants)]
        queryset = sorted(queryset, key=lambda x: x.created_at)

        return self.paginate_and_serialize(queryset, request)

    @action(detail=True, methods=["POST"])
    def update_likes(self, request, pk=None):
        comment = self.get_object()
        user = request.user

        if comment.likes.filter(id=user.id).exists():
            comment.likes.remove(user)
            liked = False
        else:
            comment.likes.add(user)
            liked = True

        comment.refresh_from_db()

        serializer = self.get_serializer(comment)
        data = serializer.data
        data["action"] = "liked" if liked else "unliked"

        return Response(data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["GET"])
    def post(self, request, pk=None):
        comment = self.get_object()
        post = comment.post
        serializer = BlogPostDetailSerializer(
            post, context={"request": request}
        )
        return Response(serializer.data)

    @action(detail=False, methods=["POST"])
    def liked_comments(self, request, *args, **kwargs):
        serializer = BlogCommentLikedCommentsRequestSerializer(
            data=request.data
        )
        serializer.is_valid(raise_exception=True)

        comment_ids = serializer.validated_data["comment_ids"]
        user = request.user

        liked_ids = list(
            BlogComment.objects.filter(
                id__in=comment_ids, likes=user
            ).values_list("id", flat=True)
        )

        response_serializer = BlogCommentLikedCommentsResponseSerializer(
            {"liked_comment_ids": liked_ids}
        )
        return Response(response_serializer.data)

    @action(detail=False, methods=["GET"])
    def my_comments(self, request):
        queryset = self.get_queryset().filter(user=request.user)
        return self.paginate_and_serialize(queryset, request)

    @action(detail=False, methods=["POST"])
    def my_comment(self, request, *args, **kwargs) -> Response:
        serializer = BlogCommentMyCommentRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        post_id = serializer.validated_data["post"]

        try:
            post = BlogPost.objects.get(id=post_id)
        except BlogPost.DoesNotExist:
            return Response(
                {"error": "Post not found"}, status=status.HTTP_404_NOT_FOUND
            )

        try:
            comment = self.get_queryset().get(user=request.user, post=post)
            serializer = self.get_serializer(comment)
            return Response(serializer.data)
        except BlogComment.DoesNotExist:
            return Response(
                {"error": "Comment not found for this post"},
                status=status.HTTP_404_NOT_FOUND,
            )
