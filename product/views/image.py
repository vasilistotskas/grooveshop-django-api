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
from product.models.image import ProductImage
from product.serializers.image import (
    ProductImageDetailSerializer,
    ProductImageSerializer,
    ProductImageWriteSerializer,
)

req_serializers: RequestSerializersConfig = {
    "create": ProductImageWriteSerializer,
    "update": ProductImageWriteSerializer,
    "partial_update": ProductImageWriteSerializer,
}

res_serializers: ResponseSerializersConfig = {
    "create": ProductImageDetailSerializer,
    "list": ProductImageSerializer,
    "retrieve": ProductImageDetailSerializer,
    "update": ProductImageDetailSerializer,
    "partial_update": ProductImageDetailSerializer,
}


@extend_schema_view(
    **create_schema_view_config(
        model_class=ProductImage,
        display_config={
            "tag": "Product Images",
        },
        request_serializers=req_serializers,
        response_serializers=res_serializers,
    )
)
@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class ProductImageViewSet(BaseModelViewSet):
    queryset = ProductImage.objects.all()
    response_serializers = res_serializers
    request_serializers = req_serializers
    filterset_fields = [
        "id",
        "product",
        "is_main",
        "sort_order",
        "created_at",
        "updated_at",
    ]
    ordering_fields = [
        "id",
        "created_at",
        "updated_at",
        "is_main",
        "sort_order",
    ]
    ordering = ["-is_main", "sort_order", "-created_at"]

    def get_queryset(self):
        queryset = ProductImage.objects.all()

        if self.action == "list":
            return queryset.optimized_for_list().ordered_by_position()
        elif self.action in ["retrieve", "update", "partial_update", "destroy"]:
            return queryset.with_product_data()

        return queryset
