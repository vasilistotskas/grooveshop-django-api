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
    SerializersConfig,
    create_schema_view_config,
    crud_config,
)
from core.utils.views import cache_methods

serializers_config: SerializersConfig = {
    **crud_config(
        list=TaggedItemSerializer,
        detail=TaggedItemDetailSerializer,
        write=TaggedItemWriteSerializer,
    ),
}


@extend_schema_view(
    **create_schema_view_config(
        model_class=TaggedItem,
        display_config={
            "tag": "Tagged Items",
        },
        serializers_config=serializers_config,
        error_serializer=ErrorResponseSerializer,
    )
)
@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class TaggedItemViewSet(BaseModelViewSet):
    queryset = TaggedItem.objects.all()
    serializers_config = serializers_config
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
