from __future__ import annotations

from typing import TYPE_CHECKING

from django.conf import settings
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_view
from rest_framework import status
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
from product.models.category_image import ProductCategoryImage
from product.serializers.category_image import (
    ProductCategoryImageBulkResponseSerializer,
    ProductCategoryImageBulkUpdateSerializer,
    ProductCategoryImageDetailSerializer,
    ProductCategoryImageSerializer,
    ProductCategoryImageWriteSerializer,
)

if TYPE_CHECKING:
    from django.db.models import QuerySet

serializers_config: SerializersConfig = {
    **crud_config(
        list=ProductCategoryImageSerializer,
        detail=ProductCategoryImageDetailSerializer,
        write=ProductCategoryImageWriteSerializer,
    ),
    "bulk_update": ActionConfig(
        request=ProductCategoryImageBulkUpdateSerializer,
        response=ProductCategoryImageBulkResponseSerializer,
        operation_id="bulkUpdateProductCategoryImages",
        summary=_("Bulk update category images"),
        description=_(
            "Update multiple category images at once. Can update active status and sort order."
        ),
        tags=["Product Category Images"],
    ),
    "by_category": ActionConfig(
        response=ProductCategoryImageSerializer,
        many=True,
        operation_id="getProductCategoryImagesByCategory",
        summary=_("Get images by category"),
        description=_("Retrieve all images for a specific category."),
        tags=["Product Category Images"],
    ),
    "by_type": ActionConfig(
        response=ProductCategoryImageSerializer,
        many=True,
        operation_id="getProductCategoryImagesByType",
        summary=_("Get images by type"),
        description=_(
            "Retrieve all images of a specific type (main, banner, icon, etc.)."
        ),
        tags=["Product Category Images"],
    ),
}


@extend_schema_view(
    **create_schema_view_config(
        model_class=ProductCategoryImage,
        display_config={
            "tag": "Product Category Images",
        },
        serializers_config=serializers_config,
        error_serializer=ErrorResponseSerializer,
    )
)
@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class ProductCategoryImageViewSet(BaseModelViewSet):
    queryset = ProductCategoryImage.objects.select_related("category")
    serializers_config = serializers_config
    filterset_fields = ["id", "category", "image_type", "active"]
    ordering_fields = ["created_at", "image_type", "sort_order"]
    ordering = ["sort_order", "-created_at"]
    search_fields = ["translations__title", "translations__alt_text"]

    def get_queryset(self) -> QuerySet[ProductCategoryImage]:
        queryset = super().get_queryset()

        category_id = self.request.query_params.get("category")
        if category_id:
            queryset = queryset.filter(category_id=category_id)

        image_type = self.request.query_params.get("image_type")
        if image_type:
            queryset = queryset.filter(image_type=image_type)

        active_only = self.request.query_params.get("active_only")
        if active_only and active_only.lower() == "true":
            queryset = queryset.filter(active=True)

        return queryset.distinct()

    @action(detail=False, methods=["patch"])
    def bulk_update(self, request):
        request_serializer_class = self.get_request_serializer()
        request_serializer = request_serializer_class(data=request.data)
        request_serializer.is_valid(raise_exception=True)

        image_ids = request_serializer.validated_data["image_ids"]
        update_data = {}

        if "active" in request_serializer.validated_data:
            update_data["active"] = request_serializer.validated_data["active"]

        if "sort_order" in request_serializer.validated_data:
            update_data["sort_order"] = request_serializer.validated_data[
                "sort_order"
            ]

        if not update_data:
            return Response(
                {"error": "No fields to update provided."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        updated_count = ProductCategoryImage.objects.filter(
            id__in=image_ids
        ).update(**update_data)

        response_data = {
            "success": True,
            "message": f"Successfully updated {updated_count} category images.",
            "updated_count": updated_count,
        }

        response_serializer_class = self.get_response_serializer()
        response_serializer = response_serializer_class(response_data)
        return Response(response_serializer.data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], pagination_class=None)
    def by_category(self, request):
        category_id = request.query_params.get("category_id")
        if not category_id:
            return Response(
                {"error": "category_id parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            category_id = int(category_id)
        except ValueError:
            return Response(
                {"error": "category_id must be a valid integer."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        images = ProductCategoryImage.objects.filter(category_id=category_id)

        response_serializer_class = self.get_response_serializer()
        response_serializer = response_serializer_class(
            images, many=True, context=self.get_serializer_context()
        )
        return Response(response_serializer.data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], pagination_class=None)
    def by_type(self, request):
        image_type = request.query_params.get("image_type")
        if not image_type:
            return Response(
                {"error": "image_type parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        images = ProductCategoryImage.objects.filter(image_type=image_type)

        response_serializer_class = self.get_response_serializer()
        response_serializer = response_serializer_class(
            images, many=True, context=self.get_serializer_context()
        )
        return Response(response_serializer.data, status=status.HTTP_200_OK)
