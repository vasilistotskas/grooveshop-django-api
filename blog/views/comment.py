from __future__ import annotations

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from blog.models.comment import BlogComment
from blog.serializers.comment import BlogCommentSerializer
from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter


class BlogCommentViewSet(BaseModelViewSet):
    queryset = BlogComment.objects.all()
    serializer_class = BlogCommentSerializer
    filter_backends = [DjangoFilterBackend, PascalSnakeCaseOrderingFilter, SearchFilter]
    filterset_fields = ["id", "user", "post", "parent", "is_approved"]
    ordering_fields = ["id", "user", "post", "created_at"]
    ordering = ["id"]
    search_fields = ["id", "user", "post"]

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

    @action(detail=False, methods=["POST"])
    def user_blog_comment(self, request, *args, **kwargs) -> Response:
        user_id = request.data.get("user")
        post_id = request.data.get("post")

        if not user_id or not post_id:
            return Response(
                {"detail": "User and Post are required fields"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            comment = BlogComment.objects.get(user=user_id, post=post_id)
            serializer = self.get_serializer(comment)
            return Response(serializer.data, status=status.HTTP_200_OK)

        except BlogComment.DoesNotExist:
            return Response(
                {"detail": "Comment does not exist"},
                status=status.HTTP_404_NOT_FOUND,
            )

        except ValueError:
            return Response(
                {"detail": "Invalid data"},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @action(detail=True, methods=["POST"], permission_classes=[IsAuthenticated])
    def update_likes(self, request, pk=None) -> Response:
        if not request.user.is_authenticated:
            return Response(
                {"detail": "Authentication credentials were not provided."},
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

    @action(detail=True, methods=["GET"])
    def replies(self, request, pk=None) -> Response:
        comment = self.get_object()
        if not comment.get_children().exists():
            return Response(
                {"detail": "No replies found"}, status=status.HTTP_404_NOT_FOUND
            )

        replies_queryset = comment.get_children()
        pagination_param = request.query_params.get("pagination", "true").lower()

        if pagination_param == "false":
            serializer = BlogCommentSerializer(
                replies_queryset, many=True, context=self.get_serializer_context()
            )
            return Response(serializer.data, status=status.HTTP_200_OK)

        page = self.paginate_queryset(replies_queryset)
        if page is not None:
            serializer = BlogCommentSerializer(
                page, many=True, context=self.get_serializer_context()
            )
            return self.get_paginated_response(serializer.data)

        serializer = BlogCommentSerializer(
            replies_queryset, many=True, context=self.get_serializer_context()
        )
        return Response(serializer.data, status=status.HTTP_200_OK)
