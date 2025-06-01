from __future__ import annotations

from django.conf import settings
from django.utils.translation import gettext_lazy as _
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.filters import SearchFilter

from core.api.serializers import ErrorResponseSerializer
from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.views import cache_methods
from product.models.image import ProductImage
from product.serializers.image import ProductImageSerializer


@extend_schema_view(
    list=extend_schema(
        summary=_("List product images"),
        description=_(
            "Retrieve a list of product images with filtering and search capabilities."
        ),
        tags=["Product Images"],
        responses={
            200: ProductImageSerializer(many=True),
        },
    ),
    create=extend_schema(
        summary=_("Create a product image"),
        description=_(
            "Upload a new image for a product. Supports multi-language titles. Requires authentication."
        ),
        tags=["Product Images"],
        responses={
            201: ProductImageSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
        },
    ),
    retrieve=extend_schema(
        summary=_("Retrieve a product image"),
        description=_(
            "Get detailed information about a specific product image."
        ),
        tags=["Product Images"],
        responses={
            200: ProductImageSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    update=extend_schema(
        summary=_("Update a product image"),
        description=_(
            "Update product image information including the image file. Requires authentication."
        ),
        tags=["Product Images"],
        responses={
            200: ProductImageSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    partial_update=extend_schema(
        summary=_("Partially update a product image"),
        description=_(
            "Partially update product image information. Requires authentication."
        ),
        tags=["Product Images"],
        responses={
            200: ProductImageSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    destroy=extend_schema(
        summary=_("Delete a product image"),
        description=_("Delete a product image. Requires authentication."),
        tags=["Product Images"],
        responses={
            204: None,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
)
@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class ProductImageViewSet(BaseModelViewSet):
    queryset = ProductImage.objects.all()
    serializer_class = ProductImageSerializer
    filter_backends = [
        DjangoFilterBackend,
        PascalSnakeCaseOrderingFilter,
        SearchFilter,
    ]
    filterset_fields = ["id", "product", "is_main"]
    ordering_fields = ["created_at", "is_main"]
    ordering = ["-is_main", "-created_at"]
