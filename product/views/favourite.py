from __future__ import annotations

from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.decorators import action

from rest_framework.response import Response

from core.api.views import BaseModelViewSet

from core.utils.serializers import (
    MultiSerializerMixin,
    create_schema_view_config,
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

schema_config = create_schema_view_config(
    model_class=ProductFavourite,
    display_config={
        "tag": "Product Favourites",
    },
    serializers={
        "list_serializer": ProductFavouriteSerializer,
        "detail_serializer": ProductFavouriteDetailSerializer,
        "write_serializer": ProductFavouriteWriteSerializer,
    },
)


@extend_schema_view(**schema_config)
class ProductFavouriteViewSet(MultiSerializerMixin, BaseModelViewSet):
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
    serializers = {
        "default": ProductFavouriteDetailSerializer,
        "list": ProductFavouriteSerializer,
        "retrieve": ProductFavouriteDetailSerializer,
        "create": ProductFavouriteWriteSerializer,
        "update": ProductFavouriteWriteSerializer,
        "partial_update": ProductFavouriteWriteSerializer,
        "product": ProductDetailResponseSerializer,
        "favourites_by_products": ProductFavouriteByProductsResponseSerializer,
    }

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(user=request.user)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

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
        serializer = self.get_serializer(
            product_favourite.product, context=self.get_serializer_context()
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

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
                "items": {"$ref": "#/components/schemas/ProductFavourite"},
            },
        },
    )
    @action(detail=False, methods=["POST"])
    def favourites_by_products(self, request, *args, **kwargs):
        user = request.user
        serializer = ProductFavouriteByProductsRequestSerializer(
            data=request.data
        )
        serializer.is_valid(raise_exception=True)

        product_ids = serializer.validated_data["product_ids"]
        favourites = ProductFavourite.objects.filter(
            user=user, product_id__in=product_ids
        ).select_related("user", "product")

        response_serializer = self.get_serializer(favourites, many=True)
        return Response(response_serializer.data, status=status.HTTP_200_OK)
