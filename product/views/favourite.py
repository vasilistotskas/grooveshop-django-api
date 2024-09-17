from __future__ import annotations

from typing import override

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.decorators import throttle_classes
from rest_framework.filters import SearchFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.api.throttling import BurstRateThrottle
from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.serializers import MultiSerializerMixin
from product.models.favourite import ProductFavourite
from product.serializers.favourite import ProductFavouriteSerializer
from product.serializers.product import ProductSerializer


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

    @throttle_classes([BurstRateThrottle])
    @override
    def create(self, request, *args, **kwargs) -> Response:
        return super().create(request, *args, **kwargs)

    @action(detail=True, methods=["GET"])
    def product(self, request, *args, **kwargs) -> Response:
        product_favourite = self.get_object()
        serializer = self.get_serializer(product_favourite.product, context=self.get_serializer_context())
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["POST"], permission_classes=[IsAuthenticated])
    def favourites_by_products(self, request, *args, **kwargs):
        user = request.user
        product_ids = request.data.get("product_ids", [])
        if not product_ids:
            return Response(
                {"error": "No product IDs provided."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        favourites = ProductFavourite.objects.filter(user=user, product_id__in=product_ids)
        serializer = self.get_serializer(favourites, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
