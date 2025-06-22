from __future__ import annotations

from django.conf import settings
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema_view
from rest_framework.filters import SearchFilter

from blog.filters.tag import BlogTagFilter
from blog.models.tag import BlogTag
from blog.serializers.tag import (
    BlogTagDetailSerializer,
    BlogTagSerializer,
    BlogTagWriteSerializer,
)
from core.api.serializers import ErrorResponseSerializer
from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.serializers import (
    MultiSerializerMixin,
    create_schema_view_config,
)
from core.utils.views import cache_methods


@extend_schema_view(
    **create_schema_view_config(
        model_class=BlogTag,
        display_config={
            "tag": "Blog Tags",
        },
        serializers={
            "list_serializer": BlogTagSerializer,
            "detail_serializer": BlogTagDetailSerializer,
            "write_serializer": BlogTagWriteSerializer,
        },
        error_serializer=ErrorResponseSerializer,
        additional_responses={
            "create": {201: BlogTagDetailSerializer},
            "update": {200: BlogTagDetailSerializer},
            "partial_update": {200: BlogTagDetailSerializer},
        },
    )
)
@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class BlogTagViewSet(MultiSerializerMixin, BaseModelViewSet):
    queryset = BlogTag.objects.all()
    serializers = {
        "default": BlogTagDetailSerializer,
        "list": BlogTagSerializer,
        "retrieve": BlogTagDetailSerializer,
        "create": BlogTagWriteSerializer,
        "update": BlogTagWriteSerializer,
        "partial_update": BlogTagWriteSerializer,
    }
    response_serializers = {
        "create": BlogTagDetailSerializer,
        "update": BlogTagDetailSerializer,
        "partial_update": BlogTagDetailSerializer,
    }
    filter_backends = [
        DjangoFilterBackend,
        PascalSnakeCaseOrderingFilter,
        SearchFilter,
    ]
    filterset_class = BlogTagFilter
    ordering_fields = [
        "id",
        "active",
        "created_at",
        "updated_at",
        "sort_order",
    ]
    ordering = ["-created_at"]
    search_fields = ["translations__name"]
