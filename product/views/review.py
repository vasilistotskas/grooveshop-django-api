from __future__ import annotations

from typing import override

from django.utils.translation import gettext_lazy as _
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.api.throttling import BurstRateThrottle
from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.serializers import MultiSerializerMixin
from product.enum.review import ReviewStatusEnum
from product.models.review import ProductReview
from product.serializers.product import ProductSerializer
from product.serializers.review import ProductReviewSerializer


class ProductReviewViewSet(MultiSerializerMixin, BaseModelViewSet):
    filter_backends = [
        DjangoFilterBackend,
        PascalSnakeCaseOrderingFilter,
        SearchFilter,
    ]
    filterset_fields = ["id", "user_id", "product_id", "status"]
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
        "default": ProductReviewSerializer,
        "product": ProductSerializer,
    }

    @override
    def get_queryset(self):
        if self.request.user.is_superuser:
            return ProductReview.objects.all()
        return ProductReview.objects.filter(status=ReviewStatusEnum.TRUE)

    @override
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

    @action(
        detail=False,
        methods=["POST"],
        throttle_classes=[BurstRateThrottle],
        permission_classes=[IsAuthenticated],
    )
    def user_product_review(self, request, *args, **kwargs) -> Response:
        if not request.user.is_authenticated:
            return Response(
                {"detail": _("User is not authenticated")},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        product_id = request.data.get("product")
        user_id = request.user.id

        if not user_id or not product_id:
            return Response(
                {"detail": _("User and Product are required fields")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            review = ProductReview.objects.get(user_id=user_id, product_id=product_id)
            serializer = self.get_serializer(review)
            return Response(serializer.data, status=status.HTTP_200_OK)

        except ProductReview.DoesNotExist:
            return Response(
                {"detail": _("Review does not exist")},
                status=status.HTTP_404_NOT_FOUND,
            )

        except ValueError:
            return Response(
                {"detail": _("Invalid data")},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @action(detail=True, methods=["GET"])
    def product(self, request, *args, **kwargs) -> Response:
        product_review = self.get_object()
        serializer = self.get_serializer(product_review.product, context=self.get_serializer_context())
        return Response(serializer.data, status=status.HTTP_200_OK)
