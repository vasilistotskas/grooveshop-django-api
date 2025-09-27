from __future__ import annotations

from django.conf import settings
from django.db.models import Count
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import (
    extend_schema,
    extend_schema_view,
)
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from blog.filters.comment import BlogCommentFilter
from blog.models.comment import BlogComment
from blog.serializers.comment import (
    BlogCommentDetailSerializer,
    BlogCommentLikedCommentsRequestSerializer,
    BlogCommentLikedCommentsResponseSerializer,
    BlogCommentSerializer,
    BlogCommentWriteSerializer,
)
from blog.serializers.post import (
    BlogPostDetailSerializer,
)
from core.api.permissions import IsOwnerOrAdmin
from core.api.serializers import ErrorResponseSerializer
from core.api.views import BaseModelViewSet

from core.utils.serializers import (
    create_schema_view_config,
    RequestSerializersConfig,
    ResponseSerializersConfig,
)
from core.utils.views import cache_methods

req_serializers: RequestSerializersConfig = {
    "create": BlogCommentWriteSerializer,
    "update": BlogCommentWriteSerializer,
    "partial_update": BlogCommentWriteSerializer,
    "liked_comments": BlogCommentLikedCommentsRequestSerializer,
}

res_serializers: ResponseSerializersConfig = {
    "create": BlogCommentDetailSerializer,
    "list": BlogCommentSerializer,
    "retrieve": BlogCommentDetailSerializer,
    "update": BlogCommentDetailSerializer,
    "partial_update": BlogCommentDetailSerializer,
    "replies": BlogCommentSerializer,
    "thread": BlogCommentSerializer,
    "update_likes": BlogCommentDetailSerializer,
    "post": BlogPostDetailSerializer,
    "liked_comments": BlogCommentLikedCommentsResponseSerializer,
    "my_comments": BlogCommentSerializer,
}


@extend_schema_view(
    **create_schema_view_config(
        model_class=BlogComment,
        display_config={
            "tag": "Blog Comments",
        },
        request_serializers=req_serializers,
        response_serializers=res_serializers,
        error_serializer=ErrorResponseSerializer,
    )
)
@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class BlogCommentViewSet(BaseModelViewSet):
    queryset = BlogComment.objects.all()
    request_serializers = req_serializers
    response_serializers = res_serializers

    filterset_class = BlogCommentFilter
    ordering_fields = [
        "id",
        "created_at",
        "updated_at",
        "level",
        "lft",
        "approved",
    ]
    ordering = ["-created_at"]
    search_fields = [
        "translations__content",
        "user__email",
        "user__first_name",
        "user__last_name",
        "post__translations__title",
    ]

    def get_queryset(self):
        queryset = BlogComment.objects.select_related(
            "user", "post", "parent", "post__category", "post__author"
        ).prefetch_related(
            "likes",
            "translations",
            "children",
            "post__translations",
        )

        if not self.request.user.is_staff:
            queryset = queryset.filter(approved=True)

        return queryset

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
        ]:
            permission_classes.append(IsOwnerOrAdmin)
        return [permission() for permission in permission_classes]

    @extend_schema(
        operation_id="listBlogCommentReplies",
        summary=_("Get comment replies"),
        description=_(
            "Get all replies (children) of this comment in threaded structure."
        ),
        tags=["Blog Comments"],
        responses={
            200: BlogCommentSerializer(many=True),
            404: ErrorResponseSerializer,
        },
    )
    @action(detail=True, methods=["GET"])
    def replies(self, request, pk=None):
        comment = self.get_object()
        queryset = (
            comment.get_children()
            .select_related("user", "post")
            .prefetch_related("likes")
            .filter(approved=True)
            .order_by("created_at")
        )

        response_serializer_class = self.get_response_serializer()
        return self.paginate_and_serialize(
            queryset, request, serializer_class=response_serializer_class
        )

    @extend_schema(
        operation_id="getBlogCommentThread",
        summary=_("Get comment thread"),
        description=_(
            "Get the complete thread (all ancestors and descendants) of this comment."
        ),
        tags=["Blog Comments"],
        responses={
            200: BlogCommentSerializer(many=True),
        },
    )
    @action(detail=True, methods=["GET"])
    def thread(self, request, pk=None):
        comment = self.get_object()

        if self.request.user.is_staff:
            ancestors = comment.get_ancestors().select_related("user", "post")
            descendants = comment.get_descendants().select_related(
                "user", "post"
            )
        else:
            ancestors = (
                comment.get_ancestors()
                .select_related("user", "post")
                .filter(approved=True)
            )
            descendants = (
                comment.get_descendants()
                .select_related("user", "post")
                .filter(approved=True)
            )

        queryset = [*list(ancestors), comment, *list(descendants)]
        queryset = sorted(queryset, key=lambda x: x.created_at)

        response_serializer_class = self.get_response_serializer()
        return self.paginate_and_serialize(
            queryset, request, serializer_class=response_serializer_class
        )

    @extend_schema(
        operation_id="toggleBlogCommentLike",
        summary=_("Toggle comment like"),
        description=_("Like or unlike a comment. Toggles the like status."),
        tags=["Blog Comments"],
        request=None,
        responses={
            200: BlogCommentDetailSerializer,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    )
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

        response_serializer_class = self.get_response_serializer()
        response_serializer = response_serializer_class(
            comment, context=self.get_serializer_context()
        )
        data = response_serializer.data.copy()
        data["action"] = "liked" if liked else "unliked"

        return Response(data, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="getBlogCommentPost",
        summary=_("Get comment's blog post"),
        description=_("Get the blog post that this comment belongs to."),
        tags=["Blog Comments"],
        responses={
            200: BlogPostDetailSerializer,
            404: ErrorResponseSerializer,
        },
    )
    @action(detail=True, methods=["GET"])
    def post(self, request, pk=None):
        self.ordering_fields = []
        self.ordering = []
        self.search_fields = []

        comment = self.get_object()
        post = comment.post

        response_serializer_class = self.get_response_serializer()
        response_serializer = response_serializer_class(
            post, context=self.get_serializer_context()
        )
        return Response(response_serializer.data)

    @extend_schema(
        operation_id="checkBlogCommentLikes",
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
    )
    @action(detail=False, methods=["POST"])
    def liked_comments(self, request, *args, **kwargs):
        request_serializer_class = self.get_request_serializer()
        serializer = request_serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        comment_ids = serializer.validated_data["comment_ids"]
        user = request.user

        queryset = BlogComment.objects.filter(id__in=comment_ids, likes=user)
        if not self.request.user.is_staff:
            queryset = queryset.filter(approved=True)

        liked_ids = list(queryset.values_list("id", flat=True))

        response_data = {"liked_comment_ids": liked_ids}

        response_serializer_class = self.get_response_serializer()
        response_serializer = response_serializer_class(response_data)
        return Response(response_serializer.data)

    @extend_schema(
        operation_id="listMyBlogComments",
        summary=_("Get current user's comments"),
        description=_(
            "Get all comments made by the currently authenticated user."
        ),
        tags=["Blog Comments"],
        responses={
            200: BlogCommentSerializer(many=True),
            401: ErrorResponseSerializer,
        },
    )
    @action(detail=False, methods=["GET"])
    def my_comments(self, request):
        queryset = self.get_queryset().filter(user=request.user)

        if (
            hasattr(queryset.query.where, "children")
            and queryset.query.where.children
        ):
            queryset = (
                BlogComment.objects.select_related(
                    "user", "post", "parent", "post__category", "post__author"
                )
                .prefetch_related(
                    "likes",
                    "translations",
                    "children",
                    "post__translations",
                )
                .annotate(
                    likes_count_field=Count("likes", distinct=True),
                )
                .filter(user=request.user)
            )

        response_serializer_class = self.get_response_serializer()
        return self.paginate_and_serialize(
            queryset, request, serializer_class=response_serializer_class
        )
