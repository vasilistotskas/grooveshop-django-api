from __future__ import annotations

from typing import override

from django.utils.translation import gettext_lazy as _
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.decorators import throttle_classes
from rest_framework.filters import SearchFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from blog.models.comment import BlogComment
from blog.serializers.comment import BlogCommentSerializer
from blog.serializers.post import BlogPostSerializer
from core.api.throttling import BurstRateThrottle
from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.serializers import MultiSerializerMixin


class BlogCommentViewSet(MultiSerializerMixin, BaseModelViewSet):
    queryset = BlogComment.objects.all()
    filter_backends = [
        DjangoFilterBackend,
        PascalSnakeCaseOrderingFilter,
        SearchFilter,
    ]
    filterset_fields = ["id", "user", "post", "parent", "is_approved"]
    ordering_fields = ["id", "user", "post", "created_at"]
    ordering = ["-created_at"]
    search_fields = ["id", "user", "post"]

    serializers = {
        "default": BlogCommentSerializer,
        "post": BlogPostSerializer,
    }

    @override
    def get_permissions(self):
        if self.action in [
            "create",
            "update",
            "partial_update",
            "destroy",
            "user_blog_comment",
        ]:
            self.permission_classes = [IsAuthenticated]
        return super().get_permissions()

    @throttle_classes([BurstRateThrottle])
    @override
    def create(self, request, *args, **kwargs) -> Response:
        return super().create(request, *args, **kwargs)

    @action(
        detail=False,
        methods=["POST"],
        permission_classes=[IsAuthenticated],
        throttle_classes=[BurstRateThrottle],
    )
    def user_blog_comment(self, request, *args, **kwargs) -> Response:
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
            comment = BlogComment.objects.get(user=user_id, post=post_id, parent=None)
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

    @action(
        detail=True,
        methods=["POST"],
        permission_classes=[IsAuthenticated],
        throttle_classes=[BurstRateThrottle],
    )
    def update_likes(self, request, pk=None) -> Response:
        if not request.user.is_authenticated:
            return Response(
                {"detail": _("Authentication credentials were not provided.")},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        comment = self.get_object()
        user = request.user

        if comment.likes.contains(user):
            comment.likes.remove(user)
        else:
            comment.likes.add(user)
        comment.save()
        serializer = self.get_serializer(comment, context=self.get_serializer_context())
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["GET"], throttle_classes=[BurstRateThrottle])
    def replies(self, request, pk=None) -> Response:
        comment = self.get_object()
        if not comment.get_children().exists():
            return Response(
                {"detail": _("No replies found")},
                status=status.HTTP_404_NOT_FOUND,
            )

        queryset = comment.get_children()
        return self.paginate_and_serialize(queryset, request)

    @action(detail=True, methods=["GET"])
    def post(self, request, pk=None) -> Response:
        comment = self.get_object()
        serializer = self.get_serializer(comment.post, context=self.get_serializer_context())
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["POST"], permission_classes=[IsAuthenticated])
    def liked_comments(self, request, *args, **kwargs):
        user = request.user
        comment_ids = request.data.get("comment_ids", [])
        if not comment_ids:
            return Response(
                {"error": _("No comment IDs provided.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        liked_comment_ids = BlogComment.objects.filter(likes=user, id__in=comment_ids).values_list("id", flat=True)

        return Response(liked_comment_ids, status=status.HTTP_200_OK)
