from __future__ import annotations

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.response import Response

from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from product.filters.product import ProductFilter
from product.models.product import Product
from product.serializers.image import ProductImageSerializer
from product.serializers.product import ProductSerializer
from product.serializers.review import ProductReviewSerializer


class ProductViewSet(BaseModelViewSet):
    queryset = Product.objects.all()
    serializer_class = ProductSerializer
    filter_backends = [DjangoFilterBackend, PascalSnakeCaseOrderingFilter, SearchFilter]
    filterset_class = ProductFilter
    ordering_fields = [
        "price",
        "created_at",
        "discount_value",
        "final_price",
        "price_save_percent",
        "review_average",
        "likes_count",
    ]
    ordering = ["-created_at"]
    search_fields = ["id"]

    @action(detail=True, methods=["POST"])
    def update_product_hits(self, request, pk=None, *args, **kwargs) -> Response:
        product = self.get_object()
        data = {"hits": product.hits + 1}
        serializer = self.get_serializer(product, data=data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(
        detail=True,
        methods=["GET"],
    )
    def reviews(self, request, pk=None) -> Response:
        product = self.get_object()
        reviews = product.reviews.all()
        serializer = ProductReviewSerializer(
            reviews, many=True, context=self.get_serializer_context()
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(
        detail=True,
        methods=["GET"],
    )
    def images(self, request, pk=None) -> Response:
        product = self.get_object()
        images = product.product_images.all()
        serializer = ProductImageSerializer(
            images, many=True, context=self.get_serializer_context()
        )
        return Response(serializer.data, status=status.HTTP_200_OK)
