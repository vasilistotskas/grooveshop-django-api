from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.api.permissions import IsSelfOrAdmin
from order.models.item import OrderItem
from order.serializers.item import (
    OrderItemRefundSerializer,
    OrderItemSerializer,
)


@extend_schema_view(
    list=extend_schema(
        description="List all order items associated with the authenticated user's orders.",
        tags=["order-items"],
    ),
    retrieve=extend_schema(
        description="Retrieve a specific order item by ID.",
        tags=["order-items"],
    ),
    refund=extend_schema(
        description="Process a refund for an order item.",
        tags=["order-items"],
    ),
)
class OrderItemViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = OrderItemSerializer
    permission_classes = [IsAuthenticated, IsSelfOrAdmin]

    def get_queryset(self):
        user = self.request.user
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
        if not order.user or order.user.id != self.request.user.id:
            raise PermissionDenied(
                _("You do not have permission to access this order.")
            )

    @action(detail=True, methods=["post"])
    def refund(self, request, pk=None):
        order_item = self.get_object()

        if order_item.order.status not in [
            "DELIVERED",
            "COMPLETED",
            "RETURNED",
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
                    "item": OrderItemSerializer(order_item).data,
                }
            )
        except ValidationError as e:
            raise DRFValidationError({"detail": str(e)}) from e
        except Exception as e:
            import logging

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
