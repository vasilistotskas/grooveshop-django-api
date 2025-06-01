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
from product.models.category import ProductCategory
from product.serializers.category import ProductCategorySerializer


@extend_schema_view(
    list=extend_schema(
        summary=_("List product categories"),
        description=_(
            "Retrieve a list of product categories with filtering and search capabilities."
        ),
        tags=["Product Categories"],
        responses={
            200: ProductCategorySerializer(many=True),
        },
    ),
    create=extend_schema(
        summary=_("Create a product category"),
        description=_(
            "Create a new product category. Requires authentication."
        ),
        tags=["Product Categories"],
        responses={
            201: ProductCategorySerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
        },
    ),
    retrieve=extend_schema(
        summary=_("Retrieve a product category"),
        description=_(
            "Get detailed information about a specific product category, including its hierarchy and related data."
        ),
        tags=["Product Categories"],
        responses={
            200: ProductCategorySerializer,
            404: ErrorResponseSerializer,
        },
    ),
    update=extend_schema(
        summary=_("Update a product category"),
        description=_(
            "Update product category information. Requires authentication."
        ),
        tags=["Product Categories"],
        responses={
            200: ProductCategorySerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    partial_update=extend_schema(
        summary=_("Partially update a product category"),
        description=_(
            "Partially update product category information. Requires authentication."
        ),
        tags=["Product Categories"],
        responses={
            200: ProductCategorySerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    destroy=extend_schema(
        summary=_("Delete a product category"),
        description=_("Delete a product category. Requires authentication."),
        tags=["Product Categories"],
        responses={
            204: None,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
)
@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class ProductCategoryViewSet(BaseModelViewSet):
    queryset = ProductCategory.objects.all()
    serializer_class = ProductCategorySerializer
    filter_backends = [
        DjangoFilterBackend,
        PascalSnakeCaseOrderingFilter,
        SearchFilter,
    ]
    filterset_fields = ["id"]
    ordering_fields = [
        "id",
        "created_at",
    ]
    ordering = ["-created_at"]
    search_fields = ["id"]
