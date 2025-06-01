from __future__ import annotations

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
from product.models.favourite import ProductFavourite
from product.serializers.favourite import (
    ProductFavouriteByProductsRequestSerializer,
    ProductFavouriteSerializer,
)
from product.serializers.product import ProductSerializer


@extend_schema_view(
    list=extend_schema(
        summary=_("List product favourites"),
        description=_(
            "Retrieve a list of product favourites with filtering and search capabilities."
        ),
        tags=["Product Favourites"],
        responses={
            200: ProductFavouriteSerializer(many=True),
        },
    ),
    create=extend_schema(
        summary=_("Create a product favourite"),
        description=_(
            "Add a product to user's favourites. Requires authentication."
        ),
        tags=["Product Favourites"],
        responses={
            201: ProductFavouriteSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
        },
    ),
    retrieve=extend_schema(
        summary=_("Retrieve a product favourite"),
        description=_(
            "Get detailed information about a specific product favourite."
        ),
        tags=["Product Favourites"],
        responses={
            200: ProductFavouriteSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    update=extend_schema(
        summary=_("Update a product favourite"),
        description=_(
            "Update product favourite information. Requires authentication."
        ),
        tags=["Product Favourites"],
        responses={
            200: ProductFavouriteSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    partial_update=extend_schema(
        summary=_("Partially update a product favourite"),
        description=_(
            "Partially update product favourite information. Requires authentication."
        ),
        tags=["Product Favourites"],
        responses={
            200: ProductFavouriteSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    destroy=extend_schema(
        summary=_("Delete a product favourite"),
        description=_(
            "Remove a product from user's favourites. Requires authentication."
        ),
        tags=["Product Favourites"],
        responses={
            204: None,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    product=extend_schema(
        summary=_("Get favourite product details"),
        description=_(
            "Get detailed information about the product in this favourite entry."
        ),
        tags=["Product Favourites"],
        responses={
            200: ProductSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    favourites_by_products=extend_schema(
        summary=_("Get favourites by product IDs"),
        description=_(
            "Get favourite entries for the specified product IDs. Requires authentication."
        ),
        tags=["Product Favourites"],
        request=ProductFavouriteByProductsRequestSerializer,
        responses={
            200: ProductFavouriteSerializer(many=True),
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
        },
    ),
)
class ProductFavouriteViewSet(MultiSerializerMixin, BaseModelViewSet):
    queryset = ProductFavourite.objects.all()
    filter_backends = [
        DjangoFilterBackend,
        PascalSnakeCaseOrderingFilter,
        SearchFilter,
    ]
    filterset_fields = ["id", "user_id", "product_id"]
    ordering_fields = [
        "id",
        "user_id",
        "product_id",
        "created_at",
    ]
    ordering = ["-created_at"]
    search_fields = [
        "id",
        "user_id",
        "product_id",
    ]

    serializers = {
        "default": ProductFavouriteSerializer,
        "product": ProductSerializer,
        "products": ProductSerializer,
    }

    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    @action(detail=True, methods=["GET"])
    def product(self, request, *args, **kwargs):
        product_favourite = self.get_object()
        serializer = self.get_serializer(
            product_favourite.product, context=self.get_serializer_context()
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["POST"])
    def favourites_by_products(self, request, *args, **kwargs):
        user = request.user
        product_ids = request.data.get("product_ids", [])
        if not product_ids:
            return Response(
                {"error": _("No product IDs provided.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        favourites = ProductFavourite.objects.filter(
            user=user, product_id__in=product_ids
        )
        serializer = self.get_serializer(favourites, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
