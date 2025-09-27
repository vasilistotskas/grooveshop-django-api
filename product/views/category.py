from __future__ import annotations

from django.conf import settings
from drf_spectacular.utils import extend_schema_view


from core.api.views import BaseModelViewSet
from core.utils.serializers import (
    create_schema_view_config,
    RequestSerializersConfig,
    ResponseSerializersConfig,
)
from core.utils.views import cache_methods
from product.filters.category import ProductCategoryFilter
from product.models.category import ProductCategory
from product.serializers.category import (
    ProductCategoryDetailSerializer,
    ProductCategorySerializer,
    ProductCategoryWriteSerializer,
)

req_serializers: RequestSerializersConfig = {
    "create": ProductCategoryWriteSerializer,
    "update": ProductCategoryWriteSerializer,
    "partial_update": ProductCategoryWriteSerializer,
}

res_serializers: ResponseSerializersConfig = {
    "create": ProductCategoryDetailSerializer,
    "list": ProductCategorySerializer,
    "retrieve": ProductCategoryDetailSerializer,
    "update": ProductCategoryDetailSerializer,
    "partial_update": ProductCategoryDetailSerializer,
}


@extend_schema_view(
    **create_schema_view_config(
        model_class=ProductCategory,
        display_config={
            "tag": "Product Categories",
        },
        request_serializers=req_serializers,
        response_serializers=res_serializers,
    )
)
@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class ProductCategoryViewSet(BaseModelViewSet):
    queryset = ProductCategory.objects.all()
    filterset_class = ProductCategoryFilter
    response_serializers = res_serializers
    request_serializers = req_serializers
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
