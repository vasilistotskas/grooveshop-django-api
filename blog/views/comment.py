from __future__ import annotations

from django.conf import settings
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
    MultiSerializerMixin,
    create_schema_view_config,
    RequestSerializersConfig,
    ResponseSerializersConfig,
)
from core.utils.views import cache_methods


req_serializers: RequestSerializersConfig = {
    "create": BlogCommentWriteSerializer,
    "update": BlogCommentWriteSerializer,
    "partial_update": BlogCommentWriteSerializer,
}

res_serializers: ResponseSerializersConfig = {
    "create": BlogCommentDetailSerializer,
    "list": BlogCommentSerializer,
    "retrieve": BlogCommentDetailSerializer,
    "update": BlogCommentDetailSerializer,
    "partial_update": BlogCommentDetailSerializer,
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
class BlogCommentViewSet(MultiSerializerMixin, BaseModelViewSet):
    queryset = BlogComment.objects.all()
    serializers = {
        "default": BlogCommentDetailSerializer,
        "list": BlogCommentSerializer,
        "retrieve": BlogCommentDetailSerializer,
        "create": BlogCommentWriteSerializer,
        "update": BlogCommentWriteSerializer,
        "partial_update": BlogCommentWriteSerializer,
        "replies": BlogCommentSerializer,
        "thread": BlogCommentSerializer,
        "update_likes": BlogCommentDetailSerializer,
        "post": BlogPostDetailSerializer,
        "liked_comments": BlogCommentLikedCommentsResponseSerializer,
        "my_comments": BlogCommentSerializer,
    }
    response_serializers = {
        "create": BlogCommentDetailSerializer,
        "update": BlogCommentDetailSerializer,
        "partial_update": BlogCommentDetailSerializer,
    }
    filterset_class = BlogCommentFilter
    ordering_fields = [
        "id",
        "created_at",
        "updated_at",
        "level",
        "lft",
        "approved",
        "likes_count_field",
        "replies_count_field",
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
        from django.db.models import Count

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
                replies_count_field=Count("children", distinct=True),
            )
        )

        if self.action == "list" and not self.request.user.is_staff:
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
        return self.paginate_and_serialize(queryset, request)

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
        ancestors = comment.get_ancestors().select_related("user", "post")
        descendants = comment.get_descendants().select_related("user", "post")

        queryset = [*list(ancestors), comment, *list(descendants)]
        queryset = sorted(queryset, key=lambda x: x.created_at)

        return self.paginate_and_serialize(queryset, request)

    @extend_schema(
        operation_id="toggleBlogCommentLike",
        summary=_("Toggle comment like"),
        description=_("Like or unlike a comment. Toggles the like status."),
        tags=["Blog Comments"],
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

        serializer = self.get_serializer(comment)
        data = serializer.data
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
        comment = self.get_object()
        post = comment.post
        serializer = BlogPostDetailSerializer(
            post, context={"request": request}
        )
        return Response(serializer.data)

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
        return self.paginate_and_serialize(queryset, request)
