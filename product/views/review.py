from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.api.serializers import ErrorResponseSerializer
from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.serializers import (
    MultiSerializerMixin,
    create_schema_view_config,
)
from product.enum.review import ReviewStatus
from product.filters.review import ProductReviewFilter
from product.models.review import ProductReview
from product.serializers.product import ProductSerializer
from product.serializers.review import (
    ProductReviewDetailSerializer,
    ProductReviewSerializer,
    ProductReviewWriteSerializer,
    UserProductReviewRequestSerializer,
)


@extend_schema_view(
    **create_schema_view_config(
        model_class=ProductReview,
        display_config={
            "tag": "Product Reviews",
        },
        serializers={
            "list_serializer": ProductReviewSerializer,
            "detail_serializer": ProductReviewDetailSerializer,
            "write_serializer": ProductReviewWriteSerializer,
        },
        error_serializer=ErrorResponseSerializer,
    )
)
class ProductReviewViewSet(MultiSerializerMixin, BaseModelViewSet):
    filter_backends = [
        DjangoFilterBackend,
        PascalSnakeCaseOrderingFilter,
        SearchFilter,
    ]

    filterset_class = ProductReviewFilter

    ordering_fields = [
        "id",
        "user_id",
        "product_id",
        "rate",
        "created_at",
        "updated_at",
        "published_at",
    ]

    ordering = ["-created_at"]

    search_fields = [
        "product__translations__name",
        "user__email",
        "translations__comment",
    ]

    serializers = {
        "default": ProductReviewDetailSerializer,
        "list": ProductReviewSerializer,
        "retrieve": ProductReviewDetailSerializer,
        "create": ProductReviewWriteSerializer,
        "update": ProductReviewWriteSerializer,
        "partial_update": ProductReviewWriteSerializer,
        "product": ProductSerializer,
    }

    response_serializers = {
        "create": ProductReviewDetailSerializer,
        "update": ProductReviewDetailSerializer,
        "partial_update": ProductReviewDetailSerializer,
    }

    def get_queryset(self):
        queryset = ProductReview.objects.with_product_details()

        if self.request.user.is_superuser:
            return queryset

        if self.request.user.is_authenticated:
            return queryset.filter(
                models.Q(status=ReviewStatus.TRUE)
                | models.Q(user=self.request.user)
            )
        else:
            return queryset.filter(status=ReviewStatus.TRUE)

    def get_permissions(self):
        if self.action in [
            "create",
            "update",
            "partial_update",
            "destroy",
            "user_product_review",
        ]:
            self.permission_classes = [IsAuthenticated]
        return super().get_permissions()

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @extend_schema(
        operation_id="getUserProductReview",
        summary=_("Get user's review for a product"),
        description=_(
            "Get the current user's review for a specific product. Requires authentication."
        ),
        tags=["Product Reviews"],
        request=UserProductReviewRequestSerializer,
        responses={
            200: ProductReviewDetailSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    )
    @action(detail=False, methods=["POST"])
    def user_product_review(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return Response(
                {"detail": _("User is not authenticated")},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        serializer = UserProductReviewRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                serializer.errors,
                status=status.HTTP_400_BAD_REQUEST,
            )

        product_id = serializer.validated_data["product"]
        user_id = request.user.id

        try:
            review = ProductReview.objects.get(
                user_id=user_id, product_id=product_id
            )
            review_serializer = ProductReviewDetailSerializer(
                review, context=self.get_serializer_context()
            )
            return Response(review_serializer.data, status=status.HTTP_200_OK)

        except ProductReview.DoesNotExist:
            return Response(
                {"detail": _("Review does not exist")},
                status=status.HTTP_404_NOT_FOUND,
            )

    @extend_schema(
        operation_id="getProductReviewProduct",
        summary=_("Get reviewed product details"),
        description=_(
            "Get detailed information about the product this review is for."
        ),
        tags=["Product Reviews"],
        responses={
            200: ProductSerializer,
            404: ErrorResponseSerializer,
        },
    )
    @action(detail=True, methods=["GET"])
    def product(self, request, *args, **kwargs):
        review = self.get_object()
        product = review.product
        serializer = ProductSerializer(
            product, context=self.get_serializer_context()
        )
        return Response(serializer.data, status=status.HTTP_200_OK)
