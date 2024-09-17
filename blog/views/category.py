from __future__ import annotations

from django.conf import settings
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
from core.utils.views import cache_methods


@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve", "posts"])
class BlogCategoryViewSet(MultiSerializerMixin, BaseModelViewSet):
    queryset = BlogCategory.objects.all()
    filter_backends = [
        DjangoFilterBackend,
        PascalSnakeCaseOrderingFilter,
        SearchFilter,
    ]
    filterset_fields = ["id"]
    ordering_fields = ["id", "created_at"]
    ordering = ["-updated_at"]
    search_fields = ["id"]

    serializers = {
        "default": BlogCategorySerializer,
        "posts": BlogPostSerializer,
    }

    @action(detail=True, methods=["GET"])
    def posts(self, request, pk=None, *args, **kwargs) -> Response:
        self.ordering_fields = [
            "created_at",
            "updated_at",
            "published_at",
        ]
        queryset = self.get_object().blog_posts.all()
        return self.paginate_and_serialize(queryset, request)
