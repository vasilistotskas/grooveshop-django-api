from __future__ import annotations

from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import (
    extend_schema,
    extend_schema_view,
)
from rest_framework import status
from rest_framework.decorators import action

from rest_framework.response import Response

from core.api.serializers import ErrorResponseSerializer
from core.api.views import BaseModelViewSet
from core.utils.serializers import (
    create_schema_view_config,
    RequestSerializersConfig,
    ResponseSerializersConfig,
)
from product.filters.favourite import ProductFavouriteFilter
from product.models.favourite import ProductFavourite
from product.serializers.favourite import (
    ProductDetailResponseSerializer,
    ProductFavouriteByProductsRequestSerializer,
    ProductFavouriteByProductsResponseSerializer,
    ProductFavouriteDetailSerializer,
    ProductFavouriteSerializer,
    ProductFavouriteWriteSerializer,
)

req_serializers: RequestSerializersConfig = {
    "create": ProductFavouriteWriteSerializer,
    "update": ProductFavouriteWriteSerializer,
    "partial_update": ProductFavouriteWriteSerializer,
    "favourites_by_products": ProductFavouriteByProductsRequestSerializer,
}

res_serializers: ResponseSerializersConfig = {
    "create": ProductFavouriteWriteSerializer,
    "list": ProductFavouriteSerializer,
    "retrieve": ProductFavouriteDetailSerializer,
    "update": ProductFavouriteWriteSerializer,
    "partial_update": ProductFavouriteWriteSerializer,
    "product": ProductDetailResponseSerializer,
    "favourites_by_products": ProductFavouriteByProductsResponseSerializer,
}

schema_config = create_schema_view_config(
    model_class=ProductFavourite,
    display_config={
        "tag": "Product Favourites",
    },
    request_serializers=req_serializers,
    response_serializers=res_serializers,
)


@extend_schema_view(**schema_config)
class ProductFavouriteViewSet(BaseModelViewSet):
    queryset = ProductFavourite.objects.select_related(
        "user", "product"
    ).prefetch_related("product__translations")
    filterset_class = ProductFavouriteFilter
    ordering_fields = [
        "id",
        "user_id",
        "product_id",
        "created_at",
        "updated_at",
    ]
    ordering = ["-created_at"]
    search_fields = [
        "user__username",
        "product__translations__name",
    ]
    response_serializers = res_serializers
    request_serializers = req_serializers

    def create(self, request, *args, **kwargs):
        req_serializer = self.get_request_serializer()
        request_serializer = req_serializer(
            data=request.data, context=self.get_serializer_context()
        )
        request_serializer.is_valid(raise_exception=True)
        request_serializer.save(user=request.user)

        response_serializer_class = self.get_response_serializer()
        response_serializer = response_serializer_class(
            request_serializer.instance, context=self.get_serializer_context()
        )

        headers = self.get_success_headers(response_serializer.data)
        return Response(
            response_serializer.data,
            status=status.HTTP_201_CREATED,
            headers=headers,
        )

    @extend_schema(
        operation_id="getProductFavouriteProduct",
        summary=_("Get favourite product details"),
        description=_(
            "Get detailed information about the product in this favourite entry."
        ),
        tags=["Product Favourites"],
        responses={
            200: ProductDetailResponseSerializer,
        },
    )
    @action(detail=True, methods=["GET"])
    def product(self, request, *args, **kwargs):
        product_favourite = self.get_object()

        self.ordering_fields = []
        self.ordering = []
        self.search_fields = []

        response_serializer_class = self.get_response_serializer()
        response_serializer = response_serializer_class(
            product_favourite.product, context=self.get_serializer_context()
        )
        return Response(response_serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="getProductFavouritesByProducts",
        summary=_("Get favourites by product IDs"),
        description=_(
            "Get favourite entries for the specified product IDs. Requires authentication."
        ),
        tags=["Product Favourites"],
        request=ProductFavouriteByProductsRequestSerializer,
        responses={
            200: {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "userId": {"type": "integer"},
                        "productId": {"type": "integer"},
                        "createdAt": {"type": "string", "format": "date-time"},
                    },
                    "required": ["id", "userId", "productId", "createdAt"],
                },
            },
            404: ErrorResponseSerializer,
        },
        parameters=[],
    )
    @action(detail=False, methods=["POST"])
    def favourites_by_products(self, request, *args, **kwargs):
        user = request.user
        request_serializer_class = self.get_request_serializer()
        request_serializer = request_serializer_class(data=request.data)
        request_serializer.is_valid(raise_exception=True)

        product_ids = request_serializer.validated_data["product_ids"]
        favourites = ProductFavourite.objects.filter(
            user=user, product_id__in=product_ids
        ).select_related("user", "product")

        response_serializer_class = self.get_response_serializer()
        response_serializer = response_serializer_class(favourites, many=True)
        return Response(response_serializer.data, status=status.HTTP_200_OK)
