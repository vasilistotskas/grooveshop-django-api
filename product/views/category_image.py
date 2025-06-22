from __future__ import annotations

from typing import TYPE_CHECKING

from django.conf import settings
from django.utils.translation import gettext_lazy as _
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.response import Response

from core.api.serializers import ErrorResponseSerializer
from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.serializers import (
    MultiSerializerMixin,
    create_schema_view_config,
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

schema_config = create_schema_view_config(
    model_class=ProductCategoryImage,
    display_config={
        "tag": "Product Category Images",
    },
    serializers={
        "list_serializer": ProductCategoryImageSerializer,
        "detail_serializer": ProductCategoryImageDetailSerializer,
        "write_serializer": ProductCategoryImageWriteSerializer,
    },
)


@extend_schema_view(**schema_config)
@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class ProductCategoryImageViewSet(MultiSerializerMixin, BaseModelViewSet):
    queryset = ProductCategoryImage.objects.select_related("category")
    filter_backends = [
        DjangoFilterBackend,
        PascalSnakeCaseOrderingFilter,
        SearchFilter,
    ]
    filterset_fields = ["id", "category", "image_type", "active"]
    ordering_fields = ["created_at", "image_type", "sort_order"]
    ordering = ["sort_order", "-created_at"]
    search_fields = ["translations__title", "translations__alt_text"]

    serializers = {
        "default": ProductCategoryImageDetailSerializer,
        "list": ProductCategoryImageSerializer,
        "retrieve": ProductCategoryImageDetailSerializer,
        "create": ProductCategoryImageWriteSerializer,
        "update": ProductCategoryImageWriteSerializer,
        "partial_update": ProductCategoryImageWriteSerializer,
        "bulk_update": ProductCategoryImageBulkUpdateSerializer,
        "by_category": ProductCategoryImageSerializer,
        "by_type": ProductCategoryImageSerializer,
    }

    response_serializers = {
        "create": ProductCategoryImageDetailSerializer,
        "update": ProductCategoryImageDetailSerializer,
        "partial_update": ProductCategoryImageDetailSerializer,
    }

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

    @extend_schema(
        operation_id="bulkUpdateProductCategoryImages",
        summary=_("Bulk update category images"),
        description=_(
            "Update multiple category images at once. Can update active status and sort order."
        ),
        tags=["Product Category Images"],
        request=ProductCategoryImageBulkUpdateSerializer,
        responses={
            200: ProductCategoryImageBulkResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
        },
    )
    @action(detail=False, methods=["patch"])
    def bulk_update(self, request):
        serializer = ProductCategoryImageBulkUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        image_ids = serializer.validated_data["image_ids"]
        update_data = {}

        if "active" in serializer.validated_data:
            update_data["active"] = serializer.validated_data["active"]

        if "sort_order" in serializer.validated_data:
            update_data["sort_order"] = serializer.validated_data["sort_order"]

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

        return Response(response_data, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="getProductCategoryImagesByCategory",
        summary=_("Get images by category"),
        description=_("Retrieve all images for a specific category."),
        tags=["Product Category Images"],
        responses={
            200: ProductCategoryImageSerializer(many=True),
            404: ErrorResponseSerializer,
        },
    )
    @action(detail=False, methods=["get"])
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
        serializer = ProductCategoryImageSerializer(images, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="getProductCategoryImagesByType",
        summary=_("Get images by type"),
        description=_(
            "Retrieve all images of a specific type (main, banner, icon, etc.)."
        ),
        tags=["Product Category Images"],
        responses={
            200: ProductCategoryImageSerializer(many=True),
        },
    )
    @action(detail=False, methods=["get"])
    def by_type(self, request):
        image_type = request.query_params.get("image_type")
        if not image_type:
            return Response(
                {"error": "image_type parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        images = ProductCategoryImage.objects.filter(image_type=image_type)
        serializer = ProductCategoryImageSerializer(images, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
