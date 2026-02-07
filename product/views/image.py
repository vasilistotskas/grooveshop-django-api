from __future__ import annotations

from django.conf import settings
from drf_spectacular.utils import extend_schema_view


from core.api.serializers import ErrorResponseSerializer
from core.api.views import BaseModelViewSet
from core.utils.serializers import (
    SerializersConfig,
    create_schema_view_config,
    crud_config,
)
from core.utils.views import cache_methods
from product.models.image import ProductImage
from product.serializers.image import (
    ProductImageDetailSerializer,
    ProductImageSerializer,
    ProductImageWriteSerializer,
)

serializers_config: SerializersConfig = {
    **crud_config(
        list=ProductImageSerializer,
        detail=ProductImageDetailSerializer,
        write=ProductImageWriteSerializer,
    ),
}


@extend_schema_view(
    **create_schema_view_config(
        model_class=ProductImage,
        display_config={
            "tag": "Product Images",
        },
        serializers_config=serializers_config,
        error_serializer=ErrorResponseSerializer,
    )
)
@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class ProductImageViewSet(BaseModelViewSet):
    queryset = ProductImage.objects.all()
    serializers_config = serializers_config
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
        if self.action == "list":
            return ProductImage.objects.for_list()
        return ProductImage.objects.for_detail()
