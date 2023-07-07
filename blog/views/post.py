from __future__ import annotations

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from blog.models.post import BlogPost
from blog.paginators.post import BlogPostPagination
from blog.serializers.post import BlogPostSerializer
from core.api.views import BaseExpandView
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter


class BlogPostViewSet(BaseExpandView, ModelViewSet):
    queryset = BlogPost.objects.all()
    serializer_class = BlogPostSerializer
    pagination_class = BlogPostPagination
    filter_backends = [DjangoFilterBackend, PascalSnakeCaseOrderingFilter, SearchFilter]
    filterset_fields = ["id", "tags", "slug", "author"]
    ordering_fields = [
        "id",
        "title",
        "slug",
        "created_at",
        "updated_at",
        "published_at",
    ]
    ordering = ["-created_at"]
    search_fields = ["id", "title", "subtitle", "body"]

    def list(self, request, *args, **kwargs) -> Response:
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def create(self, request, *args, **kwargs) -> Response:
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def retrieve(self, request, pk=None, *args, **kwargs) -> Response:
        post = get_object_or_404(BlogPost, pk=pk)
        serializer = self.get_serializer(post)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def update(self, request, pk=None, *args, **kwargs) -> Response:
        post = get_object_or_404(BlogPost, pk=pk)
        serializer = self.get_serializer(post, data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def partial_update(self, request, pk=None, *args, **kwargs) -> Response:
        post = get_object_or_404(BlogPost, pk=pk)
        serializer = self.get_serializer(post, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def destroy(self, request, pk=None, *args, **kwargs):
        post = get_object_or_404(BlogPost, pk=pk)
        post.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated])
    def update_likes(self, request, pk=None) -> Response:
        if not request.user.is_authenticated:
            return Response(
                {"detail": "Authentication credentials were not provided."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        post = get_object_or_404(BlogPost, pk=pk)
        user = request.user

        if post.likes.contains(user):
            post.likes.remove(user)
        else:
            post.likes.add(user)
        post.save()
        serializer = self.get_serializer(post)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(
        detail=True,
        methods=["post"],
    )
    def update_view_count(self, request, pk=None) -> Response:
        post = get_object_or_404(BlogPost, pk=pk)
        post.view_count += 1
        post.save()
        serializer = self.get_serializer(post)
        return Response(serializer.data, status=status.HTTP_200_OK)
