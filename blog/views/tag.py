from __future__ import annotations

from django.utils.decorators import method_decorator
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status
from rest_framework.filters import SearchFilter
from rest_framework.response import Response

from blog.models.tag import BlogTag
from blog.serializers.tag import BlogTagSerializer
from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.views import conditional_cache_page

DEFAULT_BLOG_TAG_CACHE_TTL = 60 * 60 * 2


class BlogTagViewSet(BaseModelViewSet):
    queryset = BlogTag.objects.all()
    serializer_class = BlogTagSerializer
    filter_backends = [DjangoFilterBackend, PascalSnakeCaseOrderingFilter, SearchFilter]
    filterset_fields = ["id", "active"]
    ordering_fields = ["id", "active", "created_at"]
    ordering = ["-created_at"]
    search_fields = ["id", "translations__name"]

    @method_decorator(conditional_cache_page(DEFAULT_BLOG_TAG_CACHE_TTL))
    def list(self, request, *args, **kwargs) -> Response:
        return super().list(request, *args, **kwargs)

    @method_decorator(conditional_cache_page(DEFAULT_BLOG_TAG_CACHE_TTL))
    def retrieve(self, request, pk=None, *args, **kwargs) -> Response:
        tag = self.get_object()
        serializer = self.get_serializer(tag)
        return Response(serializer.data, status=status.HTTP_200_OK)
