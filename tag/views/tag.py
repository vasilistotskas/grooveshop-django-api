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
    SerializersConfig,
    create_schema_view_config,
    crud_config,
)
from core.utils.views import cache_methods

serializers_config: SerializersConfig = {
    **crud_config(
        list=TagSerializer,
        detail=TagDetailSerializer,
        write=TagWriteSerializer,
    ),
}


@extend_schema_view(
    **create_schema_view_config(
        model_class=Tag,
        display_config={
            "tag": "Tags",
        },
        serializers_config=serializers_config,
        error_serializer=ErrorResponseSerializer,
    )
)
@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class TagViewSet(BaseModelViewSet):
    queryset = Tag.objects.all()
    serializers_config = serializers_config
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

    def get_queryset(self):
        """
        Return optimized queryset based on action.

        Uses Tag.objects.for_list() for list views and
        Tag.objects.for_detail() for detail views.
        """
        if self.action == "list":
            return Tag.objects.for_list()
        return Tag.objects.for_detail()
