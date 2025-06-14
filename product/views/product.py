from __future__ import annotations

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
from core.utils.serializers import MultiSerializerMixin
from core.utils.views import cache_methods
from product.filters.product import ProductFilter
from product.models.product import Product
from product.serializers.image import ProductImageSerializer
from product.serializers.product import (
    ProductDetailSerializer,
    ProductListSerializer,
    ProductWriteSerializer,
)
from product.serializers.review import ProductReviewSerializer
from tag.serializers.tag import TagSerializer


@extend_schema_view(
    list=extend_schema(
        summary=_("List products"),
        description=_(
            "Retrieve a list of products with rich filtering and search capabilities. "
            "Includes computed fields like popularity scores, price categories, and stock status."
        ),
        tags=["Products"],
        responses={
            200: ProductListSerializer(many=True),
        },
    ),
    create=extend_schema(
        summary=_("Create a product"),
        description=_(
            "Create a new product with all required information. Requires authentication."
        ),
        tags=["Products"],
        request=ProductWriteSerializer,
        responses={
            201: ProductDetailSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
        },
    ),
    retrieve=extend_schema(
        summary=_("Retrieve a product"),
        description=_(
            "Get detailed information about a specific product including pricing breakdown, "
            "related products, recent reviews, and engagement metrics."
        ),
        tags=["Products"],
        responses={
            200: ProductDetailSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    update=extend_schema(
        summary=_("Update a product"),
        description=_("Update product information. Requires authentication."),
        tags=["Products"],
        request=ProductWriteSerializer,
        responses={
            200: ProductDetailSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    partial_update=extend_schema(
        summary=_("Partially update a product"),
        description=_(
            "Partially update product information. Requires authentication."
        ),
        tags=["Products"],
        request=ProductWriteSerializer,
        responses={
            200: ProductDetailSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    destroy=extend_schema(
        summary=_("Delete a product"),
        description=_("Delete a product. Requires authentication."),
        tags=["Products"],
        responses={
            204: None,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    update_view_count=extend_schema(
        summary=_("Increment product view count"),
        description=_("Increment the view count for a product."),
        tags=["Products"],
        responses={
            200: ProductDetailSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    reviews=extend_schema(
        summary=_("Get product reviews"),
        description=_("Get all reviews for a product."),
        tags=["Products"],
        responses={
            200: ProductReviewSerializer(many=True),
            404: ErrorResponseSerializer,
        },
    ),
    images=extend_schema(
        summary=_("Get product images"),
        description=_("Get all images for a product."),
        tags=["Products"],
        responses={
            200: ProductImageSerializer(many=True),
            404: ErrorResponseSerializer,
        },
    ),
    tags=extend_schema(
        summary=_("Get product tags"),
        description=_("Get all tags associated with a product."),
        tags=["Products"],
        responses={
            200: TagSerializer(many=True),
            404: ErrorResponseSerializer,
        },
    ),
)
@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class ProductViewSet(MultiSerializerMixin, BaseModelViewSet):
    queryset = Product.objects.all()
    filter_backends = [
        DjangoFilterBackend,
        PascalSnakeCaseOrderingFilter,
        SearchFilter,
    ]
    filterset_class = ProductFilter
    ordering_fields = [
        "price",
        "created_at",
        "discount_value",
        "final_price",
        "price_save_percent",
        "review_average",
        "likes_count",
        "view_count",
        "stock",
    ]
    ordering = ["-created_at"]
    search_fields = ["translations__name", "translations__description", "slug"]

    serializers = {
        "list": ProductListSerializer,
        "retrieve": ProductDetailSerializer,
        "create": ProductWriteSerializer,
        "update": ProductWriteSerializer,
        "partial_update": ProductWriteSerializer,
        "reviews": ProductReviewSerializer,
        "images": ProductImageSerializer,
        "tags": TagSerializer,
    }

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset.with_all_annotations()

    @action(
        detail=True,
        methods=["POST"],
    )
    def update_view_count(self, request, pk=None):
        product = self.get_object()
        product.view_count += 1
        product.save()
        serializer = self.get_serializer(product)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(
        detail=True,
        methods=["GET"],
    )
    def reviews(self, request, pk=None):
        product = self.get_object()
        reviews = product.reviews.all()
        serializer = self.get_serializer(
            reviews, many=True, context=self.get_serializer_context()
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(
        detail=True,
        methods=["GET"],
    )
    def images(self, request, pk=None):
        product = self.get_object()
        images = product.images.all().order_by("sort_order")
        serializer = self.get_serializer(
            images, many=True, context=self.get_serializer_context()
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(
        detail=True,
        methods=["GET"],
    )
    def tags(self, request, pk=None):
        product = self.get_object()
        tags = product.tags.filter(active=True)
        serializer = self.get_serializer(
            tags, many=True, context=self.get_serializer_context()
        )
        return Response(serializer.data, status=status.HTTP_200_OK)
