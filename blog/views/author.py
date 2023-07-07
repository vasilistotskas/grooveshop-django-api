from __future__ import annotations

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status
from rest_framework.filters import SearchFilter
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from blog.models.author import BlogAuthor
from blog.paginators.author import BlogAuthorPagination
from blog.serializers.author import BlogAuthorSerializer
from core.api.views import BaseExpandView
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter


class BlogAuthorViewSet(BaseExpandView, ModelViewSet):
    queryset = BlogAuthor.objects.all()
    serializer_class = BlogAuthorSerializer
    pagination_class = BlogAuthorPagination
    filter_backends = [DjangoFilterBackend, PascalSnakeCaseOrderingFilter, SearchFilter]
    filterset_fields = ["id", "user"]
    ordering_fields = ["id", "user", "created_at"]
    ordering = ["id"]
    search_fields = ["id", "user"]

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
        author = get_object_or_404(BlogAuthor, pk=pk)
        serializer = self.get_serializer(author)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def update(self, request, pk=None, *args, **kwargs) -> Response:
        author = get_object_or_404(BlogAuthor, pk=pk)
        serializer = self.get_serializer(author, data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def partial_update(self, request, pk=None, *args, **kwargs) -> Response:
        author = get_object_or_404(BlogAuthor, pk=pk)
        serializer = self.get_serializer(author, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def destroy(self, request, pk=None, *args, **kwargs) -> Response:
        author = get_object_or_404(BlogAuthor, pk=pk)
        author.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
