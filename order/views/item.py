from __future__ import annotations

import logging

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.exceptions import ValidationError as DRFValidationError
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
from core.utils.views import cache_methods
from order.enum.status import OrderStatus
from order.filters import OrderItemFilter
from order.models.item import OrderItem
from order.serializers.item import (
    OrderItemDetailSerializer,
    OrderItemRefundSerializer,
    OrderItemSerializer,
    OrderItemWriteSerializer,
)


@extend_schema_view(
    **create_schema_view_config(
        model_class=OrderItem,
        display_config={
            "tag": "Order Items",
        },
        serializers={
            "list_serializer": OrderItemSerializer,
            "detail_serializer": OrderItemDetailSerializer,
            "write_serializer": OrderItemWriteSerializer,
        },
    ),
    refund=extend_schema(
        operation_id="refundOrderItem",
        summary=_("Process a refund for an order item"),
        description=_("Process a refund for an order item."),
        tags=["Order Items"],
        request=OrderItemRefundSerializer,
        responses={
            200: OrderItemDetailSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
)
@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class OrderItemViewSet(MultiSerializerMixin, BaseModelViewSet):
    queryset = OrderItem.objects.all()
    serializers = {
        "default": OrderItemDetailSerializer,
        "list": OrderItemSerializer,
        "retrieve": OrderItemDetailSerializer,
        "create": OrderItemWriteSerializer,
        "update": OrderItemWriteSerializer,
        "partial_update": OrderItemWriteSerializer,
        "refund": OrderItemDetailSerializer,
    }
    response_serializers = {
        "create": OrderItemDetailSerializer,
        "update": OrderItemDetailSerializer,
        "partial_update": OrderItemDetailSerializer,
    }
    permission_classes = [IsAuthenticated]
    filter_backends = [
        DjangoFilterBackend,
        PascalSnakeCaseOrderingFilter,
        SearchFilter,
    ]
    filterset_class = OrderItemFilter
    ordering_fields = [
        "id",
        "created_at",
        "updated_at",
        "quantity",
        "price",
        "sort_order",
    ]
    ordering = ["-created_at"]
    search_fields = [
        "product__translations__name",
        "notes",
        "order__first_name",
        "order__last_name",
        "order__email",
    ]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return OrderItem.objects.none()

        user = self.request.user

        if user.is_staff or user.is_superuser:
            return (
                OrderItem.objects.all()
                .select_related("order", "product")
                .prefetch_related("product__translations")
            )

        return (
            OrderItem.objects.filter(order__user=user)
            .select_related("order", "product")
            .prefetch_related("product__translations")
        )

    def get_object(self):
        try:
            obj = super().get_object()
            self.check_order_permission(obj.order)
            return obj
        except OrderItem.DoesNotExist as e:
            raise NotFound(_("Order item not found.")) from e

    def check_order_permission(self, order):
        user = self.request.user

        if user.is_staff or user.is_superuser:
            return

        if not order.user or order.user.id != user.id:
            raise PermissionDenied(
                _("You do not have permission to access this order.")
            )

    def perform_create(self, serializer):
        product = serializer.validated_data["product"]
        if not serializer.validated_data.get("price"):
            serializer.save(price=product.price)
        else:
            serializer.save()

    @action(detail=True, methods=["POST"])
    def refund(self, request, pk=None):
        order_item = self.get_object()

        if order_item.order.status not in [
            OrderStatus.DELIVERED,
            OrderStatus.COMPLETED,
            OrderStatus.RETURNED,
        ]:
            raise DRFValidationError(
                {"detail": _("This order is not in a refundable state.")}
            )

        if order_item.is_refunded:
            raise DRFValidationError(
                {"detail": _("This item has already been fully refunded.")}
            )

        serializer = OrderItemRefundSerializer(
            data=request.data, context={"item": order_item}
        )
        serializer.is_valid(raise_exception=True)

        quantity = serializer.validated_data.get("quantity")
        reason = serializer.validated_data.get("reason", "")

        try:
            if reason:
                notes = order_item.notes or ""
                order_item.notes = f"{notes}\nRefund reason: {reason}".strip()
                order_item.save(update_fields=["notes"])

            refunded_amount = order_item.refund(quantity)

            return Response(
                {
                    "detail": _("Refund processed successfully."),
                    "refunded_amount": str(refunded_amount),
                    "item": OrderItemDetailSerializer(
                        order_item, context=self.get_serializer_context()
                    ).data,
                }
            )
        except ValidationError as e:
            logger = logging.getLogger(__name__)

            logger.error(
                f"Validation error during refund: {e!s}",
                extra={
                    "order_item_id": order_item.id,
                    "user_id": request.user.id,
                },
            )
            raise DRFValidationError(
                {
                    "detail": _(
                        "A validation error occurred while processing the refund. Please check your input and try again."
                    )
                }
            ) from e
        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.error(
                f"Error processing refund: {e!s}",
                extra={
                    "order_item_id": order_item.id,
                    "user_id": request.user.id,
                },
            )
            raise DRFValidationError(
                {
                    "detail": _(
                        "An error occurred while processing the refund. Please try again later."
                    )
                }
            ) from e
