from __future__ import annotations

from django.conf import settings
from drf_spectacular.utils import extend_schema_view

from tag.filters.tagged_item import TaggedItemFilter
from tag.models.tagged_item import TaggedItem
from tag.serializers.tagged_item import (
    TaggedItemDetailSerializer,
    TaggedItemSerializer,
    TaggedItemWriteSerializer,
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
    "create": TaggedItemWriteSerializer,
    "update": TaggedItemWriteSerializer,
    "partial_update": TaggedItemWriteSerializer,
}

res_serializers: ResponseSerializersConfig = {
    "create": TaggedItemDetailSerializer,
    "list": TaggedItemSerializer,
    "retrieve": TaggedItemDetailSerializer,
    "update": TaggedItemDetailSerializer,
    "partial_update": TaggedItemDetailSerializer,
}


@extend_schema_view(
    **create_schema_view_config(
        model_class=TaggedItem,
        display_config={
            "tag": "Tagged Items",
        },
        request_serializers=req_serializers,
        response_serializers=res_serializers,
        error_serializer=ErrorResponseSerializer,
    )
)
@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class TaggedItemViewSet(BaseModelViewSet):
    queryset = TaggedItem.objects.all()
    response_serializers = res_serializers
    request_serializers = req_serializers
    filterset_class = TaggedItemFilter
    ordering_fields = [
        "id",
        "created_at",
        "updated_at",
        "object_id",
        "tag__translations__label",
    ]
    ordering = ["-created_at"]
    search_fields = ["tag__translations__label", "content_type__model"]
