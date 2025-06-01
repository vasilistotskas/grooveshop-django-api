from __future__ import annotations

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
from core.utils.serializers import MultiSerializerMixin
from product.enum.review import ReviewStatusEnum
from product.models.review import ProductReview
from product.serializers.product import ProductSerializer
from product.serializers.review import (
    ProductReviewSerializer,
    UserProductReviewRequestSerializer,
)


@extend_schema_view(
    list=extend_schema(
        summary=_("List product reviews"),
        description=_(
            "Retrieve a list of product reviews with filtering and search capabilities. Regular users can see only approved reviews, while admins can see all reviews."
        ),
        tags=["Product Reviews"],
        responses={
            200: ProductReviewSerializer(many=True),
        },
    ),
    create=extend_schema(
        summary=_("Create a product review"),
        description=_(
            "Create a new product review. Requires authentication. Users can only create one review per product."
        ),
        tags=["Product Reviews"],
        responses={
            201: ProductReviewSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
        },
    ),
    retrieve=extend_schema(
        summary=_("Retrieve a product review"),
        description=_(
            "Get detailed information about a specific product review."
        ),
        tags=["Product Reviews"],
        responses={
            200: ProductReviewSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    update=extend_schema(
        summary=_("Update a product review"),
        description=_(
            "Update product review information. Requires authentication and ownership of the review."
        ),
        tags=["Product Reviews"],
        responses={
            200: ProductReviewSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    partial_update=extend_schema(
        summary=_("Partially update a product review"),
        description=_(
            "Partially update product review information. Requires authentication and ownership of the review."
        ),
        tags=["Product Reviews"],
        responses={
            200: ProductReviewSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    destroy=extend_schema(
        summary=_("Delete a product review"),
        description=_(
            "Delete a product review. Requires authentication and ownership of the review."
        ),
        tags=["Product Reviews"],
        responses={
            204: None,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    user_product_review=extend_schema(
        summary=_("Get user's review for a product"),
        description=_(
            "Get the current user's review for a specific product. Requires authentication."
        ),
        tags=["Product Reviews"],
        request=UserProductReviewRequestSerializer,
        responses={
            200: ProductReviewSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    product=extend_schema(
        summary=_("Get reviewed product details"),
        description=_(
            "Get detailed information about the product this review is for."
        ),
        tags=["Product Reviews"],
        responses={
            200: ProductSerializer,
            404: ErrorResponseSerializer,
        },
    ),
)
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

    def get_queryset(self):
        if self.request.user.is_superuser:
            return ProductReview.objects.all()
        return ProductReview.objects.filter(status=ReviewStatusEnum.TRUE)

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
    )
    def user_product_review(self, request, *args, **kwargs):
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
            review = ProductReview.objects.get(
                user_id=user_id, product_id=product_id
            )
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
    def product(self, request, *args, **kwargs):
        product_review = self.get_object()
        serializer = self.get_serializer(
            product_review.product, context=self.get_serializer_context()
        )
        return Response(serializer.data, status=status.HTTP_200_OK)
