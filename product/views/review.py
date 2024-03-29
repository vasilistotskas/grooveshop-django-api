from __future__ import annotations

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from product.models.review import ProductReview
from product.serializers.review import ProductReviewSerializer


class ProductReviewViewSet(BaseModelViewSet):
    queryset = ProductReview.objects.all()
    serializer_class = ProductReviewSerializer
    filter_backends = [DjangoFilterBackend, PascalSnakeCaseOrderingFilter, SearchFilter]
    filterset_fields = ["id", "user_id", "product_id", "status"]
    ordering_fields = [
        "id",
        "user_id",
        "product_id",
        "created_at",
    ]
    ordering = ["id"]
    search_fields = [
        "id",
        "user_id",
        "product_id",
    ]

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

    @action(detail=False, methods=["POST"])
    def user_product_review(self, request, *args, **kwargs) -> Response:
        user_id = request.data.get("user")
        product_id = request.data.get("product")

        if not user_id or not product_id:
            return Response(
                {"detail": "User and Product are required fields"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            review = ProductReview.objects.get(user_id=user_id, product_id=product_id)
            serializer = self.get_serializer(review)
            return Response(serializer.data, status=status.HTTP_200_OK)

        except ProductReview.DoesNotExist:
            return Response(
                {"detail": "Review does not exist"},
                status=status.HTTP_404_NOT_FOUND,
            )

        except ValueError:
            return Response(
                {"detail": "Invalid data"},
                status=status.HTTP_400_BAD_REQUEST,
            )
