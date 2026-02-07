from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_view
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from core.api.permissions import IsOwnerOrAdmin
from core.api.serializers import ErrorResponseSerializer
from core.api.views import BaseModelViewSet
from core.utils.serializers import (
    ActionConfig,
    SerializersConfig,
    create_schema_view_config,
    crud_config,
)
from product.enum.review import ReviewStatus
from product.filters.review import ProductReviewFilter
from product.models.review import ProductReview
from product.serializers.product import ProductSerializer
from product.serializers.review import (
    ProductReviewDetailSerializer,
    ProductReviewSerializer,
    ProductReviewWriteSerializer,
)

serializers_config: SerializersConfig = {
    **crud_config(
        list=ProductReviewSerializer,
        detail=ProductReviewDetailSerializer,
        write=ProductReviewWriteSerializer,
    ),
    "user_product_review": ActionConfig(
        response=ProductReviewDetailSerializer,
        operation_id="getUserProductReview",
        summary=_("Get user's review for a product"),
        description=_(
            "Get the current user's review for a specific product. Requires authentication."
        ),
        tags=["Product Reviews"],
    ),
    "product": ActionConfig(
        response=ProductSerializer,
        operation_id="getProductReviewProduct",
        summary=_("Get reviewed product details"),
        description=_(
            "Get detailed information about the product this review is for."
        ),
        tags=["Product Reviews"],
    ),
}


@extend_schema_view(
    **create_schema_view_config(
        model_class=ProductReview,
        display_config={
            "tag": "Product Reviews",
        },
        serializers_config=serializers_config,
        error_serializer=ErrorResponseSerializer,
    )
)
class ProductReviewViewSet(BaseModelViewSet):
    filterset_class = ProductReviewFilter
    ordering_fields = [
        "id",
        "user_id",
        "product_id",
        "rate",
        "status",
        "is_published",
        "created_at",
        "updated_at",
        "published_at",
    ]
    ordering = ["-created_at"]
    search_fields = [
        "product__translations__name",
        "user__email",
        "user__first_name",
        "user__last_name",
        "translations__comment",
    ]
    serializers_config = serializers_config

    def get_queryset(self):
        if self.action == "list":
            queryset = ProductReview.objects.for_list()
        else:
            queryset = ProductReview.objects.for_detail()

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
            self.permission_classes = [IsOwnerOrAdmin]
        return super().get_permissions()

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @action(detail=True, methods=["GET"])
    def user_product_review(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return Response(
                {"detail": _("User is not authenticated")},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        user_id = request.user.id

        try:
            review = ProductReview.objects.get(
                user_id=user_id, product_id=kwargs["pk"]
            )
            response_serializer_class = self.get_response_serializer()
            response_serializer = response_serializer_class(
                review, context=self.get_serializer_context()
            )
            return Response(response_serializer.data, status=status.HTTP_200_OK)

        except ProductReview.DoesNotExist:
            return Response(
                {"detail": _("Review does not exist")},
                status=status.HTTP_404_NOT_FOUND,
            )

    @action(detail=True, methods=["GET"])
    def product(self, request, *args, **kwargs):
        review = self.get_object()

        self.ordering_fields = []
        self.ordering = []
        self.search_fields = []

        product = review.product
        response_serializer_class = self.get_response_serializer()
        response_serializer = response_serializer_class(
            product, context=self.get_serializer_context()
        )
        return Response(response_serializer.data, status=status.HTTP_200_OK)
