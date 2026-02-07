from __future__ import annotations

from django.db.models import Case, When, IntegerField, Q
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import (
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
    ActionConfig,
    SerializersConfig,
    create_schema_view_config,
    crud_config,
)
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

serializers_config: SerializersConfig = {
    **crud_config(
        list=ProductSerializer,
        detail=ProductDetailSerializer,
        write=ProductWriteSerializer,
    ),
    "update_view_count": ActionConfig(
        response=ProductDetailSerializer,
        operation_id="incrementProductViews",
        summary=_("Increment product view count"),
        description=_("Increment the view count for a product."),
        tags=["Products"],
    ),
    "reviews": ActionConfig(
        response=ProductReviewSerializer,
        many=True,
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
    ),
    "images": ActionConfig(
        response=ProductImageSerializer,
        many=True,
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
    ),
    "tags": ActionConfig(
        response=TagSerializer,
        many=True,
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
    ),
}


@extend_schema_view(
    **create_schema_view_config(
        model_class=Product,
        display_config={
            "tag": "Products",
        },
        serializers_config=serializers_config,
        error_serializer=ErrorResponseSerializer,
    )
)
class ProductViewSet(BaseModelViewSet):
    """
    ViewSet for managing products.

    Products are the core entities in the e-commerce system. Each product includes
    basic information (name, description, price, stock), images, reviews, tags, and
    attributes. Products support multi-language translations.
    """

    queryset = Product.objects.all()
    serializers_config = serializers_config

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
        # For custom actions that don't use the main queryset, return None
        # to avoid FilterSet/queryset model mismatch
        if self.action in ["reviews", "images", "tags"]:
            return None
        return ProductFilter

    def get_queryset(self):
        """
        Return optimized queryset based on action.

        Uses Product.objects.for_list() for list views and
        Product.objects.for_detail() for detail views to avoid N+1 queries.
        """
        if self.action == "list":
            queryset = Product.objects.for_list()
        elif self.action in ["reviews", "images", "tags"]:
            # For action endpoints, we just need the product itself
            queryset = Product.objects.for_detail()
        else:
            queryset = Product.objects.for_detail()

        # Add availability priority annotation for ordering
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

    @action(
        detail=True,
        methods=["GET"],
        pagination_class=None,
    )
    def images(self, request, pk=None):
        product = get_object_or_404(Product, pk=pk)
        images = product.images.all()

        response_serializer_class = self.get_response_serializer()
        response_serializer = response_serializer_class(
            images, many=True, context=self.get_serializer_context()
        )
        return Response(response_serializer.data, status=status.HTTP_200_OK)

    @action(
        detail=True,
        methods=["GET"],
        pagination_class=None,
    )
    def tags(self, request, pk=None):
        product = get_object_or_404(Product, pk=pk)
        # Prefetch tags with translations to avoid N+1 queries
        tags = [
            tagged_item.tag
            for tagged_item in product.tags.select_related(
                "tag"
            ).prefetch_related("tag__translations")
        ]

        response_serializer_class = self.get_response_serializer()
        response_serializer = response_serializer_class(
            tags, many=True, context=self.get_serializer_context()
        )
        return Response(response_serializer.data, status=status.HTTP_200_OK)
