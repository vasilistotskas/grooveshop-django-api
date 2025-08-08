from __future__ import annotations

from django.conf import settings
from drf_spectacular.utils import extend_schema_view


from core.api.views import BaseModelViewSet

from core.utils.serializers import (
    MultiSerializerMixin,
    create_schema_view_config,
)
from core.utils.views import cache_methods
from product.filters.category import ProductCategoryFilter
from product.models.category import ProductCategory
from product.serializers.category import (
    ProductCategoryDetailSerializer,
    ProductCategorySerializer,
    ProductCategoryWriteSerializer,
)


@extend_schema_view(
    **create_schema_view_config(
        model_class=ProductCategory,
        display_config={
            "tag": "Product Categories",
        },
        serializers={
            "list_serializer": ProductCategorySerializer,
            "detail_serializer": ProductCategoryDetailSerializer,
            "write_serializer": ProductCategoryWriteSerializer,
        },
    )
)
@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class ProductCategoryViewSet(MultiSerializerMixin, BaseModelViewSet):
    queryset = ProductCategory.objects.all()
    filterset_class = ProductCategoryFilter
    serializers = {
        "default": ProductCategoryDetailSerializer,
        "list": ProductCategorySerializer,
        "retrieve": ProductCategoryDetailSerializer,
        "create": ProductCategoryWriteSerializer,
        "update": ProductCategoryWriteSerializer,
        "partial_update": ProductCategoryWriteSerializer,
    }
    response_serializers = {
        "create": ProductCategoryDetailSerializer,
        "update": ProductCategoryDetailSerializer,
        "partial_update": ProductCategoryDetailSerializer,
    }
    ordering_fields = [
        "id",
        "sort_order",
        "level",
        "lft",
        "rght",
        "tree_id",
        "created_at",
        "updated_at",
    ]
    ordering = ["tree_id", "lft"]
    search_fields = ["translations__name", "translations__description", "slug"]
