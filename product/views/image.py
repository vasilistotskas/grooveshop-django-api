from __future__ import annotations

from django.conf import settings
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema_view
from rest_framework.filters import SearchFilter

from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.serializers import (
    MultiSerializerMixin,
    create_schema_view_config,
)
from core.utils.views import cache_methods
from product.models.image import ProductImage
from product.serializers.image import (
    ProductImageDetailSerializer,
    ProductImageSerializer,
    ProductImageWriteSerializer,
)


@extend_schema_view(
    **create_schema_view_config(
        model_class=ProductImage,
        display_config={
            "tag": "Product Images",
        },
        serializers={
            "list_serializer": ProductImageSerializer,
            "detail_serializer": ProductImageDetailSerializer,
            "write_serializer": ProductImageWriteSerializer,
        },
    )
)
@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class ProductImageViewSet(MultiSerializerMixin, BaseModelViewSet):
    queryset = ProductImage.objects.all()
    serializers = {
        "default": ProductImageDetailSerializer,
        "list": ProductImageSerializer,
        "retrieve": ProductImageDetailSerializer,
        "create": ProductImageWriteSerializer,
        "update": ProductImageWriteSerializer,
        "partial_update": ProductImageWriteSerializer,
    }
    response_serializers = {
        "create": ProductImageDetailSerializer,
        "update": ProductImageDetailSerializer,
        "partial_update": ProductImageDetailSerializer,
    }
    filter_backends = [
        DjangoFilterBackend,
        PascalSnakeCaseOrderingFilter,
        SearchFilter,
    ]
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
