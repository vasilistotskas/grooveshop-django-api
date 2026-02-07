from __future__ import annotations

from django.conf import settings
from drf_spectacular.utils import extend_schema_view
from rest_framework.decorators import action
from rest_framework.response import Response

from core.api.serializers import ErrorResponseSerializer
from core.api.views import BaseModelViewSet
from core.utils.serializers import (
    ActionConfig,
    SerializersConfig,
    create_schema_view_config,
    crud_config,
)
from core.utils.views import cache_methods
from product.filters.category import ProductCategoryFilter
from product.models.category import ProductCategory
from product.serializers.category import (
    ProductCategoryDetailSerializer,
    ProductCategorySerializer,
    ProductCategoryWriteSerializer,
)

serializers_config: SerializersConfig = {
    **crud_config(
        list=ProductCategorySerializer,
        detail=ProductCategoryDetailSerializer,
        write=ProductCategoryWriteSerializer,
    ),
    "all": ActionConfig(
        response=ProductCategorySerializer,
        many=True,
        operation_id="listAllProductCategory",
        summary="List all product categories (unpaginated)",
        description="Retrieve all product categories without pagination. "
        "Useful for dropdowns, filters, and other UI components that need the complete list.",
        tags=["Product Categories"],
        parameters=[],
    ),
}


@extend_schema_view(
    **create_schema_view_config(
        model_class=ProductCategory,
        display_config={
            "tag": "Product Categories",
        },
        serializers_config=serializers_config,
        error_serializer=ErrorResponseSerializer,
    )
)
@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve", "all"])
class ProductCategoryViewSet(BaseModelViewSet):
    queryset = ProductCategory.objects.all()
    filterset_class = ProductCategoryFilter
    serializers_config = serializers_config
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

    def get_queryset(self):
        """
        Return optimized queryset based on action.

        Uses ProductCategory.objects.for_list() for list views and
        ProductCategory.objects.for_detail() for detail views.
        """
        if self.action in ["list", "all"]:
            return ProductCategory.objects.for_list()
        return ProductCategory.objects.for_detail()

    @action(
        detail=False,
        methods=["GET"],
        url_path="all",
        pagination_class=None,
        filter_backends=[],
    )
    def all(self, request):
        """
        Return all categories without pagination.

        This endpoint is optimized for cases where you need the complete
        category list, such as filter dropdowns or category trees.
        """
        queryset = self.get_queryset()
        serializer = ProductCategorySerializer(
            queryset, many=True, context=self.get_serializer_context()
        )
        return Response(serializer.data)
