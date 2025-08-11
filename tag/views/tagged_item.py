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
    MultiSerializerMixin,
    create_schema_view_config,
)
from core.utils.views import cache_methods


@extend_schema_view(
    **create_schema_view_config(
        model_class=TaggedItem,
        display_config={
            "tag": "Tagged Items",
        },
        serializers={
            "list_serializer": TaggedItemSerializer,
            "detail_serializer": TaggedItemDetailSerializer,
            "write_serializer": TaggedItemWriteSerializer,
        },
        error_serializer=ErrorResponseSerializer,
        additional_responses={
            "create": {201: TaggedItemDetailSerializer},
            "update": {200: TaggedItemDetailSerializer},
            "partial_update": {200: TaggedItemDetailSerializer},
        },
    )
)
@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class TaggedItemViewSet(MultiSerializerMixin, BaseModelViewSet):
    queryset = TaggedItem.objects.all()
    serializers = {
        "default": TaggedItemDetailSerializer,
        "list": TaggedItemSerializer,
        "retrieve": TaggedItemDetailSerializer,
        "create": TaggedItemWriteSerializer,
        "update": TaggedItemWriteSerializer,
        "partial_update": TaggedItemWriteSerializer,
    }
    response_serializers = {
        "create": TaggedItemDetailSerializer,
        "update": TaggedItemDetailSerializer,
        "partial_update": TaggedItemDetailSerializer,
    }
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
