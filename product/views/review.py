from __future__ import annotations

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from core.api.views import BaseExpandView
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from product.models.review import ProductReview
from product.paginators.review import ProductReviewPagination
from product.serializers.review import ProductReviewSerializer


class ProductReviewViewSet(BaseExpandView, ModelViewSet):
    queryset = ProductReview.objects.all()
    serializer_class = ProductReviewSerializer
    pagination_class = ProductReviewPagination
    filter_backends = [DjangoFilterBackend, PascalSnakeCaseOrderingFilter, SearchFilter]
    filterset_fields = ["id", "user_id", "product_id"]
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

    def list(self, request, *args, **kwargs) -> Response:
        queryset = self.filter_queryset(self.get_queryset())
        # Check for 'pagination' query parameter
        pagination_param = request.query_params.get("pagination", "true")
        if pagination_param.lower() == "false":
            # Return non-paginated response
            serializer = self.get_serializer(queryset, many=True)
            return Response(serializer.data, status=status.HTTP_200_OK)

        # Return paginated response
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def create(self, request, *args, **kwargs) -> Response:
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def retrieve(self, request, pk=None, *args, **kwargs) -> Response:
        review = get_object_or_404(ProductReview, id=pk)
        serializer = self.get_serializer(review)
        return Response(serializer.data)

    def update(self, request, pk=None, *args, **kwargs) -> Response:
        review = get_object_or_404(ProductReview, id=pk)
        serializer = self.get_serializer(review, data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def partial_update(self, request, pk=None, *args, **kwargs) -> Response:
        review = get_object_or_404(ProductReview, id=pk)
        serializer = self.get_serializer(review, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def destroy(self, request, pk=None, *args, **kwargs) -> Response:
        review = get_object_or_404(ProductReview, id=pk)
        review.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    def get_permissions(self):
        if self.action in ["create", "update", "partial_update", "destroy"]:
            self.permission_classes = [IsAuthenticated]
        return super().get_permissions()

    @action(detail=False, methods=["post"])
    def user_had_reviewed(self, request, *args, **kwargs) -> Response:
        user_id = request.data.get("user")
        product_id = request.data.get("product")

        if user_id is None or product_id is None:
            return Response(
                {"detail": "user_id and product_id must be provided"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            user_had_reviewed = ProductReview.objects.filter(
                user_id=user_id, product_id=product_id
            ).exists()

            return Response(user_had_reviewed, status=status.HTTP_200_OK)

        except ValueError:
            return Response(
                {"detail": "Invalid user_id or product_id provided"},
                status=status.HTTP_400_BAD_REQUEST,
            )
