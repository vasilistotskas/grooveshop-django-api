from __future__ import annotations

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.response import Response

from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.serializers import MultiSerializerMixin
from product.filters.product import ProductFilter
from product.models.product import Product
from product.serializers.image import ProductImageSerializer
from product.serializers.product import ProductSerializer
from product.serializers.review import ProductReviewSerializer
from tag.serializers.tag import TagSerializer


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
        "approved_review_average",
        "likes_count",
    ]
    ordering = ["-created_at"]
    search_fields = ["id"]

    serializers = {
        "default": ProductSerializer,
        "reviews": ProductReviewSerializer,
        "images": ProductImageSerializer,
        "tags": TagSerializer,
    }

    @action(
        detail=True,
        methods=["POST"],
    )
    def update_view_count(self, request, pk=None) -> Response:
        post = self.get_object()
        post.view_count += 1
        post.save()
        serializer = self.get_serializer(post)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(
        detail=True,
        methods=["GET"],
    )
    def reviews(self, request, pk=None) -> Response:
        product = self.get_object()
        reviews = product.reviews.all()
        serializer = self.get_serializer(reviews, many=True, context=self.get_serializer_context())
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(
        detail=True,
        methods=["GET"],
    )
    def images(self, request, pk=None) -> Response:
        product = self.get_object()
        images = product.images.all()
        serializer = self.get_serializer(images, many=True, context=self.get_serializer_context())
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(
        detail=True,
        methods=["GET"],
    )
    def tags(self, request, pk=None) -> Response:
        product = self.get_object()
        tags = product.get_tags_for_object()
        serializer = self.get_serializer(tags, many=True, context=self.get_serializer_context())
        return Response(serializer.data, status=status.HTTP_200_OK)
