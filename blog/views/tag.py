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
    SerializersConfig,
    create_schema_view_config,
    crud_config,
)
from core.utils.views import cache_methods

serializers_config: SerializersConfig = {
    **crud_config(
        list=BlogTagSerializer,
        detail=BlogTagDetailSerializer,
        write=BlogTagWriteSerializer,
    ),
}


@extend_schema_view(
    **create_schema_view_config(
        model_class=BlogTag,
        display_config={
            "tag": "Blog Tags",
        },
        serializers_config=serializers_config,
        error_serializer=ErrorResponseSerializer,
    )
)
@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class BlogTagViewSet(BaseModelViewSet):
    queryset = BlogTag.objects.all()
    serializers_config = serializers_config
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

    def get_queryset(self):
        if self.action == "list":
            return BlogTag.objects.for_list()
        return BlogTag.objects.for_detail()
