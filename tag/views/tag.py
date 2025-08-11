from __future__ import annotations

from django.conf import settings
from drf_spectacular.utils import extend_schema_view

from tag.filters.tag import TagFilter
from tag.models.tag import Tag
from tag.serializers.tag import (
    TagDetailSerializer,
    TagSerializer,
    TagWriteSerializer,
)
from core.api.serializers import ErrorResponseSerializer
from core.api.views import BaseModelViewSet

from core.utils.serializers import (
    MultiSerializerMixin,
    create_schema_view_config,
)
from core.utils.views import cache_methods


@extend_schema_view(
    **create_schema_view_config(
        model_class=Tag,
        display_config={
            "tag": "Tags",
        },
        serializers={
            "list_serializer": TagSerializer,
            "detail_serializer": TagDetailSerializer,
            "write_serializer": TagWriteSerializer,
        },
        error_serializer=ErrorResponseSerializer,
        additional_responses={
            "create": {201: TagDetailSerializer},
            "update": {200: TagDetailSerializer},
            "partial_update": {200: TagDetailSerializer},
        },
    )
)
@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class TagViewSet(MultiSerializerMixin, BaseModelViewSet):
    queryset = Tag.objects.all()
    serializers = {
        "default": TagDetailSerializer,
        "list": TagSerializer,
        "retrieve": TagDetailSerializer,
        "create": TagWriteSerializer,
        "update": TagWriteSerializer,
        "partial_update": TagWriteSerializer,
    }
    response_serializers = {
        "create": TagDetailSerializer,
        "update": TagDetailSerializer,
        "partial_update": TagDetailSerializer,
    }
    filterset_class = TagFilter
    ordering_fields = [
        "id",
        "active",
        "created_at",
        "updated_at",
        "sort_order",
        "translations__label",
    ]
    ordering = ["sort_order", "-created_at"]
    search_fields = ["translations__label"]
