from __future__ import annotations

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from blog.models.comment import BlogComment
from blog.paginators.comment import BlogCommentPagination
from blog.serializers.comment import BlogCommentSerializer
from core.api.views import BaseExpandView
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.views import TranslationsProcessingMixin


class BlogCommentViewSet(TranslationsProcessingMixin, BaseExpandView, ModelViewSet):
    queryset = BlogComment.objects.all()
    serializer_class = BlogCommentSerializer
    pagination_class = BlogCommentPagination
    filter_backends = [DjangoFilterBackend, PascalSnakeCaseOrderingFilter, SearchFilter]
    filterset_fields = ["id", "user", "post"]
    ordering_fields = ["id", "user", "post", "created_at"]
    ordering = ["id"]
    search_fields = ["id", "user", "post"]

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        pagination_param = request.query_params.get("pagination", "true")

        if pagination_param.lower() == "false":
            serializer = self.get_serializer(queryset, many=True)
            return Response(serializer.data, status=status.HTTP_200_OK)

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def create(self, request, *args, **kwargs) -> Response:
        request = self.process_translations_data(request)
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def retrieve(self, request, pk=None, *args, **kwargs) -> Response:
        comment = get_object_or_404(BlogComment, pk=pk)
        serializer = self.get_serializer(comment)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def update(self, request, pk=None, *args, **kwargs) -> Response:
        comment = get_object_or_404(BlogComment, pk=pk)
        request = self.process_translations_data(request)
        serializer = self.get_serializer(comment, data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def partial_update(self, request, pk=None, *args, **kwargs) -> Response:
        comment = get_object_or_404(BlogComment, pk=pk)
        request = self.process_translations_data(request)
        serializer = self.get_serializer(comment, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def destroy(self, request, pk=None, *args, **kwargs) -> Response:
        comment = get_object_or_404(BlogComment, pk=pk)
        comment.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

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
