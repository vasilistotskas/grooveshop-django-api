from __future__ import annotations

from django.utils.decorators import method_decorator
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.response import Response

from blog.models.category import BlogCategory
from blog.serializers.category import BlogCategorySerializer
from blog.serializers.post import BlogPostSerializer
from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.views import conditional_cache_page

DEFAULT_BLOG_CATEGORY_CACHE_TTL = 60 * 60 * 2


class BlogCategoryViewSet(BaseModelViewSet):
    queryset = BlogCategory.objects.all()
    serializer_class = BlogCategorySerializer
    filter_backends = [DjangoFilterBackend, PascalSnakeCaseOrderingFilter, SearchFilter]
    filterset_fields = ["id"]
    ordering_fields = ["id", "created_at"]
    ordering = ["-updated_at"]
    search_fields = ["id"]

    @method_decorator(conditional_cache_page(DEFAULT_BLOG_CATEGORY_CACHE_TTL))
    def list(self, request, *args, **kwargs) -> Response:
        return super().list(request, *args, **kwargs)

    @method_decorator(conditional_cache_page(DEFAULT_BLOG_CATEGORY_CACHE_TTL))
    def retrieve(self, request, pk=None, *args, **kwargs) -> Response:
        return super().retrieve(request, pk=pk, *args, **kwargs)

    @method_decorator(conditional_cache_page(DEFAULT_BLOG_CATEGORY_CACHE_TTL))
    @action(detail=True, methods=["GET"])
    def posts(self, request, pk=None, *args, **kwargs) -> Response:
        category = self.get_object()
        posts = category.posts.all()

        pagination_param = request.query_params.get("pagination", "true").lower()
        if pagination_param == "false":
            serializer = BlogPostSerializer(
                posts, many=True, context={"request": request}
            )
            return Response(serializer.data, status=status.HTTP_200_OK)

        page = self.paginate_queryset(posts)
        if page is not None:
            serializer = BlogPostSerializer(
                page, many=True, context={"request": request}
            )
            return self.get_paginated_response(serializer.data)

        serializer = BlogPostSerializer(posts, many=True, context={"request": request})
        return Response(serializer.data, status=status.HTTP_200_OK)
