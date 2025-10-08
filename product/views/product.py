from __future__ import annotations

from django.conf import settings
from django.db.models import Case, When, IntegerField, Q
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import (
    extend_schema,
    extend_schema_view,
    OpenApiParameter,
)
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response

from core.api.serializers import ErrorResponseSerializer
from core.api.views import BaseModelViewSet
from core.utils.serializers import (
    create_schema_view_config,
    RequestSerializersConfig,
    ResponseSerializersConfig,
)
from core.utils.views import cache_methods
from product.filters.product import ProductFilter
from product.filters.review import ProductReviewFilter
from product.models.product import Product
from product.serializers.image import ProductImageSerializer
from product.serializers.product import (
    ProductDetailSerializer,
    ProductSerializer,
    ProductWriteSerializer,
)
from product.serializers.review import ProductReviewSerializer
from tag.filters.tag import TagFilter
from tag.serializers.tag import TagSerializer

req_serializers: RequestSerializersConfig = {
    "create": ProductWriteSerializer,
    "update": ProductWriteSerializer,
    "partial_update": ProductWriteSerializer,
}

res_serializers: ResponseSerializersConfig = {
    "create": ProductDetailSerializer,
    "list": ProductSerializer,
    "retrieve": ProductDetailSerializer,
    "update": ProductDetailSerializer,
    "partial_update": ProductDetailSerializer,
    "update_view_count": ProductDetailSerializer,
    "reviews": ProductReviewSerializer,
    "images": ProductImageSerializer,
    "tags": TagSerializer,
}

schema_config = create_schema_view_config(
    model_class=Product,
    display_config={
        "tag": "Products",
    },
    request_serializers=req_serializers,
    response_serializers=res_serializers,
)


@extend_schema_view(**schema_config)
@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class ProductViewSet(BaseModelViewSet):
    queryset = Product.objects.all()
    request_serializers = req_serializers
    response_serializers = res_serializers

    ordering_fields = [
        "price",
        "created_at",
        "active",
        "availability_priority",
        "view_count",
        "stock",
    ]
    ordering = ["-availability_priority", "id"]
    search_fields = ["translations__name", "translations__description", "slug"]

    def get_filterset_class(self):
        action_filter_map = {
            "reviews": ProductReviewFilter,
            "tags": TagFilter,
        }

        if self.action in action_filter_map:
            return action_filter_map[self.action]
        elif self.action == "images":
            return None
        return ProductFilter

    def get_queryset(self):
        queryset = super().get_queryset()
        queryset = queryset.annotate(
            availability_priority=Case(
                When(Q(active=True) & Q(stock__gt=0), then=1),
                default=0,
                output_field=IntegerField(),
            )
        )
        return queryset

    @property
    def filterset_class(self):
        return self.get_filterset_class()

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

        response_serializer_class = self.get_response_serializer()
        response_serializer = response_serializer_class(
            product, context=self.get_serializer_context()
        )
        return Response(response_serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="listProductReviews",
        summary=_("Get product reviews"),
        description=_("Get all reviews for a product with pagination support."),
        tags=["Products"],
        parameters=[
            OpenApiParameter(
                name="id",
                type=int,
                location=OpenApiParameter.PATH,
                description="Product ID",
            ),
            OpenApiParameter(
                name="pagination_type",
                description=_("Pagination strategy type"),
                required=False,
                type=str,
                enum=["pageNumber", "cursor", "limitOffset"],
                default="pageNumber",
            ),
            OpenApiParameter(
                name="pagination",
                description=_("Enable or disable pagination"),
                required=False,
                type=str,
                enum=["true", "false"],
                default="true",
            ),
            OpenApiParameter(
                name="page_size",
                description=_("Number of results to return per page"),
                required=False,
                type=int,
                default=20,
            ),
        ],
        responses={
            200: {
                "type": "object",
                "properties": {
                    "links": {
                        "type": "object",
                        "properties": {
                            "next": {"type": "string", "nullable": True},
                            "previous": {"type": "string", "nullable": True},
                        },
                    },
                    "count": {"type": "integer"},
                    "total_pages": {"type": "integer"},
                    "page_size": {"type": "integer"},
                    "page_total_results": {"type": "integer"},
                    "results": {
                        "type": "array",
                        "items": {"$ref": "#/components/schemas/ProductReview"},
                    },
                },
            },
            404: ErrorResponseSerializer,
        },
    )
    @action(
        detail=True,
        methods=["GET"],
    )
    def reviews(self, request, pk=None):
        product = get_object_or_404(Product, pk=pk)
        reviews = product.reviews.all()

        response_serializer_class = self.get_response_serializer()
        return self.paginate_and_serialize(
            reviews, request, serializer_class=response_serializer_class
        )

    @extend_schema(
        operation_id="listProductImages",
        summary=_("Get product images"),
        description=_("Get all images for a product."),
        tags=["Products"],
        parameters=[
            OpenApiParameter(
                name="id",
                type=int,
                location=OpenApiParameter.PATH,
                description="Product ID",
            ),
            OpenApiParameter(
                name="language_code",
                description=_("Language code for translations (el, en, de)"),
                required=False,
                type=str,
                enum=["el", "en", "de"],
                default="el",
                location=OpenApiParameter.QUERY,
            ),
        ],
        responses={
            200: {
                "type": "array",
                "items": {"$ref": "#/components/schemas/ProductImage"},
            },
            404: ErrorResponseSerializer,
        },
    )
    @action(
        detail=True,
        methods=["GET"],
    )
    def images(self, request, pk=None):
        product = get_object_or_404(Product, pk=pk)
        images = product.images.all()

        response_serializer_class = self.get_response_serializer()
        response_serializer = response_serializer_class(
            images, many=True, context=self.get_serializer_context()
        )
        return Response(response_serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="listProductTags",
        summary=_("Get product tags"),
        description=_("Get all tags associated with a product."),
        tags=["Products"],
        parameters=[
            OpenApiParameter(
                name="id",
                type=int,
                location=OpenApiParameter.PATH,
                description="Product ID",
            ),
        ],
        responses={
            200: {
                "type": "array",
                "items": {"$ref": "#/components/schemas/Tag"},
            },
            404: ErrorResponseSerializer,
        },
    )
    @action(
        detail=True,
        methods=["GET"],
    )
    def tags(self, request, pk=None):
        product = get_object_or_404(Product, pk=pk)
        tags = [tagged_item.tag for tagged_item in product.tags.all()]

        response_serializer_class = self.get_response_serializer()
        response_serializer = response_serializer_class(
            tags, many=True, context=self.get_serializer_context()
        )
        return Response(response_serializer.data, status=status.HTTP_200_OK)
