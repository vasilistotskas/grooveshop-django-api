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
    filter_backends = [
        DjangoFilterBackend,
        PascalSnakeCaseOrderingFilter,
        SearchFilter,
    ]
    filterset_fields = ["id", "parent", "slug"]
    ordering_fields = [
        "id",
        "created_at",
        "updated_at",
    ]
    ordering = ["-created_at"]
    search_fields = ["translations__name", "translations__description", "slug"]
