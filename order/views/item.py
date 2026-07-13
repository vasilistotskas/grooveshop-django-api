from __future__ import annotations

import logging
from rest_framework.response import Response
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_view
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework import status
from rest_framework.permissions import IsAuthenticated

from core.api.serializers import ErrorResponseSerializer
from core.api.views import BaseModelViewSet

from core.utils.serializers import (
    ActionConfig,
    SerializersConfig,
    create_schema_view_config,
    crud_config,
)
from order.enum.status import OrderStatus
from order.filters import OrderItemFilter
from order.models.item import OrderItem
from order.serializers.item import (
    OrderItemDetailSerializer,
    OrderItemRefundSerializer,
    OrderItemSerializer,
    OrderItemWriteSerializer,
    OrderItemRefundResponseSerializer,
)

serializers_config: SerializersConfig = {
    **crud_config(
        list=OrderItemSerializer,
        detail=OrderItemDetailSerializer,
        write=OrderItemWriteSerializer,
    ),
    "refund": ActionConfig(
        request=OrderItemRefundSerializer,
        response=OrderItemRefundResponseSerializer,
        operation_id="refundOrderItem",
        summary=_("Process a refund for an order item"),
        description=_("Process a refund for an order item."),
        tags=["Order Items"],
    ),
}


@extend_schema_view(
    **create_schema_view_config(
        model_class=OrderItem,
        display_config={
            "tag": "Order Items",
        },
        serializers_config=serializers_config,
        error_serializer=ErrorResponseSerializer,
    )
)
class OrderItemViewSet(BaseModelViewSet):
    queryset = OrderItem.objects.none()
    serializers_config = serializers_config
    permission_classes = [IsAuthenticated]
    filterset_class = OrderItemFilter
    ordering_fields = [
        "id",
        "created_at",
        "updated_at",
        "quantity",
        "price",
        "sort_order",
    ]
    ordering = ["sort_order", "-created_at"]
    search_fields = [
        "product__translations__name",
        "notes",
        "order__first_name",
        "order__last_name",
        "order__email",
    ]

    def get_queryset(self):
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
        # Attaching an item to an order is only allowed for the order's owner
        # (or staff); otherwise any authenticated user could add items to
        # arbitrary orders by posting a foreign order id.
        self.check_order_permission(serializer.validated_data["order"])
        product = serializer.validated_data["product"]
        if not serializer.validated_data.get("price"):
            serializer.save(price=product.price)
        else:
            serializer.save()

    def perform_update(self, serializer):
        # `order` is a writable FK, so an update could reassign the item to a
        # different order. get_object() only validated the item's CURRENT
        # order — the TARGET order must be owned too, or an owner could move an
        # item into a foreign order. `order` may be absent on a PATCH.
        target_order = serializer.validated_data.get("order")
        if target_order is not None:
            self.check_order_permission(target_order)
        serializer.save()

    @action(detail=True, methods=["POST"])
    def refund(self, request, pk=None):
        # Refunding restocks merchant inventory and flips refund state; it is a
        # staff/merchant operation, never customer self-service (which would
        # otherwise let an order owner restock stock and mark items refunded
        # with no payment refund).
        if not (request.user.is_staff or request.user.is_superuser):
            raise PermissionDenied(_("Only staff can process refunds."))

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

        request_serializer_class = self.get_request_serializer()
        serializer = request_serializer_class(
            data=request.data,
            context={"item": order_item, **self.get_serializer_context()},
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

            response_data = {
                "detail": _("Refund processed successfully."),
                "refunded_amount": refunded_amount,
                "item": order_item,
            }

            response_serializer_class = self.get_response_serializer()
            response_serializer = response_serializer_class(response_data)
            return Response(response_serializer.data, status=status.HTTP_200_OK)

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
