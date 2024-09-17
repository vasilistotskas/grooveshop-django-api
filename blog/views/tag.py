from __future__ import annotations

from django.conf import settings
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter

from blog.models.tag import BlogTag
from blog.serializers.tag import BlogTagSerializer
from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.views import cache_methods


@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class BlogTagViewSet(BaseModelViewSet):
    queryset = BlogTag.objects.all()
    serializer_class = BlogTagSerializer
    filter_backends = [
        DjangoFilterBackend,
        PascalSnakeCaseOrderingFilter,
        SearchFilter,
    ]
    filterset_fields = ["id", "active"]
    ordering_fields = ["id", "active", "created_at"]
    ordering = ["-created_at"]
    search_fields = ["id", "translations__name"]
