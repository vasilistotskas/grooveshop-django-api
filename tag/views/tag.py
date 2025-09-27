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
    create_schema_view_config,
    RequestSerializersConfig,
    ResponseSerializersConfig,
)
from core.utils.views import cache_methods

req_serializers: RequestSerializersConfig = {
    "create": TagWriteSerializer,
    "update": TagWriteSerializer,
    "partial_update": TagWriteSerializer,
}

res_serializers: ResponseSerializersConfig = {
    "create": TagDetailSerializer,
    "list": TagSerializer,
    "retrieve": TagDetailSerializer,
    "update": TagDetailSerializer,
    "partial_update": TagDetailSerializer,
}


@extend_schema_view(
    **create_schema_view_config(
        model_class=Tag,
        display_config={
            "tag": "Tags",
        },
        request_serializers=req_serializers,
        response_serializers=res_serializers,
        error_serializer=ErrorResponseSerializer,
    )
)
@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class TagViewSet(BaseModelViewSet):
    queryset = Tag.objects.all()
    response_serializers = res_serializers
    request_serializers = req_serializers
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
