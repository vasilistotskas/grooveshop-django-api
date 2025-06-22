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
from core.utils.serializers import (
    MultiSerializerMixin,
    create_schema_view_config,
)
from core.utils.views import cache_methods
from product.filters.product import ProductFilter
from product.models.product import Product
from product.serializers.image import ProductImageSerializer
from product.serializers.product import (
    ProductDetailSerializer,
    ProductSerializer,
    ProductWriteSerializer,
)
from product.serializers.review import ProductReviewSerializer
from tag.serializers.tag import TagSerializer

schema_config = create_schema_view_config(
    model_class=Product,
    display_config={
        "tag": "Products",
    },
    serializers={
        "list_serializer": ProductSerializer,
        "detail_serializer": ProductDetailSerializer,
        "write_serializer": ProductWriteSerializer,
    },
)


@extend_schema_view(**schema_config)
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
        "discount_value_amount",
        "final_price_amount",
        "price_save_percent_field",
        "review_average_field",
        "likes_count_field",
        "view_count",
        "stock",
    ]
    ordering = ["-created_at"]
    search_fields = ["translations__name", "translations__description", "slug"]

    serializers = {
        "default": ProductDetailSerializer,
        "list": ProductSerializer,
        "retrieve": ProductDetailSerializer,
        "create": ProductWriteSerializer,
        "update": ProductWriteSerializer,
        "partial_update": ProductWriteSerializer,
        "update_view_count": ProductDetailSerializer,
        "reviews": ProductReviewSerializer,
        "images": ProductImageSerializer,
        "tags": TagSerializer,
    }

    response_serializers = {
        "create": ProductDetailSerializer,
        "update": ProductDetailSerializer,
        "partial_update": ProductDetailSerializer,
    }

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset.with_all_annotations()

    @extend_schema(
        operation_id="incrementProductViews",
        summary=_("Increment product view count"),
        description=_("Increment the view count for a product."),
        tags=["Products"],
        responses={
            200: ProductDetailSerializer,
            404: ErrorResponseSerializer,
        },
    )
    @action(
        detail=True,
        methods=["POST"],
    )
    def update_view_count(self, request, pk=None):
        product = self.get_object()
        product.view_count += 1
        product.save()
        serializer = ProductDetailSerializer(
            product, context=self.get_serializer_context()
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="listProductReviews",
        summary=_("Get product reviews"),
        description=_("Get all reviews for a product."),
        tags=["Products"],
        responses={
            200: ProductReviewSerializer(many=True),
            404: ErrorResponseSerializer,
        },
    )
    @action(
        detail=True,
        methods=["GET"],
    )
    def reviews(self, request, pk=None):
        product = self.get_object()
        reviews = product.reviews.all()
        serializer = ProductReviewSerializer(
            reviews, many=True, context=self.get_serializer_context()
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="listProductImages",
        summary=_("Get product images"),
        description=_("Get all images for a product."),
        tags=["Products"],
        responses={
            200: ProductImageSerializer(many=True),
            404: ErrorResponseSerializer,
        },
    )
    @action(
        detail=True,
        methods=["GET"],
    )
    def images(self, request, pk=None):
        product = self.get_object()
        images = product.images.all()
        serializer = ProductImageSerializer(
            images, many=True, context=self.get_serializer_context()
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="listProductTags",
        summary=_("Get product tags"),
        description=_("Get all tags associated with a product."),
        tags=["Products"],
        parameters=[],
        responses={
            200: TagSerializer(many=True),
            404: ErrorResponseSerializer,
        },
    )
    @action(
        detail=True,
        methods=["GET"],
    )
    def tags(self, request, pk=None):
        product = self.get_object()
        tags = [tagged_item.tag for tagged_item in product.tags.all()]
        serializer = TagSerializer(
            tags, many=True, context=self.get_serializer_context()
        )
        return Response(serializer.data, status=status.HTTP_200_OK)
