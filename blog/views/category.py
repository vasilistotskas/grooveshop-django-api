from __future__ import annotations

from django.utils.decorators import method_decorator
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.response import Response

from blog.models.category import BlogCategory
from blog.serializers.category import BlogCategorySerializer
from blog.serializers.post import BlogPostSerializer
from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.serializers import MultiSerializerMixin
from core.utils.views import conditional_cache_page

DEFAULT_BLOG_CATEGORY_CACHE_TTL = 60 * 60 * 2


class BlogCategoryViewSet(MultiSerializerMixin, BaseModelViewSet):
    queryset = BlogCategory.objects.all()
    filter_backends = [DjangoFilterBackend, PascalSnakeCaseOrderingFilter, SearchFilter]
    filterset_fields = ["id"]
    ordering_fields = ["id", "created_at"]
    ordering = ["-updated_at"]
    search_fields = ["id"]

    serializers = {
        "default": BlogCategorySerializer,
        "posts": BlogPostSerializer,
    }

    @method_decorator(conditional_cache_page(DEFAULT_BLOG_CATEGORY_CACHE_TTL))
    def list(self, request, *args, **kwargs) -> Response:
        return super().list(request, *args, **kwargs)

    @method_decorator(conditional_cache_page(DEFAULT_BLOG_CATEGORY_CACHE_TTL))
    def retrieve(self, request, pk=None, *args, **kwargs) -> Response:
        return super().retrieve(request, pk=pk, *args, **kwargs)

    @method_decorator(conditional_cache_page(DEFAULT_BLOG_CATEGORY_CACHE_TTL))
    @action(detail=True, methods=["GET"])
    def posts(self, request, pk=None, *args, **kwargs) -> Response:
        self.ordering_fields = [
            "title",
            "created_at",
            "updated_at",
            "published_at",
        ]
        queryset = self.get_object().posts.all()
        return self.paginate_and_serialize(queryset, request)
