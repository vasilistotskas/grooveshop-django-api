from __future__ import annotations

from django.conf import settings
from drf_spectacular.utils import extend_schema_view

from blog.filters.tag import BlogTagFilter
from blog.models.tag import BlogTag
from blog.serializers.tag import (
    BlogTagDetailSerializer,
    BlogTagSerializer,
    BlogTagWriteSerializer,
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
    "create": BlogTagWriteSerializer,
    "update": BlogTagWriteSerializer,
    "partial_update": BlogTagWriteSerializer,
}

res_serializers: ResponseSerializersConfig = {
    "create": BlogTagDetailSerializer,
    "list": BlogTagSerializer,
    "retrieve": BlogTagDetailSerializer,
    "update": BlogTagDetailSerializer,
    "partial_update": BlogTagDetailSerializer,
}


@extend_schema_view(
    **create_schema_view_config(
        model_class=BlogTag,
        display_config={
            "tag": "Blog Tags",
        },
        request_serializers=req_serializers,
        response_serializers=res_serializers,
        error_serializer=ErrorResponseSerializer,
    )
)
@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class BlogTagViewSet(BaseModelViewSet):
    queryset = BlogTag.objects.all()
    response_serializers = res_serializers
    request_serializers = req_serializers
    filterset_class = BlogTagFilter
    ordering_fields = [
        "id",
        "active",
        "created_at",
        "updated_at",
        "sort_order",
        "name",
    ]
    ordering = ["sort_order", "-created_at"]
    search_fields = ["translations__name"]
