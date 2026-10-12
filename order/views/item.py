from __future__ import annotations

import logging

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_view
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.api.permissions import StoreStaffModelPermissions
from core.api.serializers import ErrorResponseSerializer
from core.api.views import BaseModelViewSet
from core.utils.serializers import (
    ActionConfig,
    SerializersConfig,
    create_schema_view_config,
    crud_config,
)
from order.enum.status import RETURN_HANDLING_STATUSES
from order.filters import OrderItemFilter
from order.models.item import OrderItem
from order.serializers.item import (
    OrderItemDetailSerializer,
    OrderItemRestockResponseSerializer,
    OrderItemRestockSerializer,
    OrderItemSerializer,
    OrderItemWriteSerializer,
)
from tenant.membership import is_store_staff

serializers_config: SerializersConfig = {
    **crud_config(
        list=OrderItemSerializer,
        detail=OrderItemDetailSerializer,
        write=OrderItemWriteSerializer,
    ),
    "restock": ActionConfig(
        request=OrderItemRestockSerializer,
        response=OrderItemRestockResponseSerializer,
        operation_id="restockOrderItem",
        summary=_("Restock returned units of an order item"),
        description=_(
            "Puts returned units of an order line back into stock and "
            "records them on the line. It moves no money: refund the "
            "payment with the order's refund action."
        ),
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
    # Reads: the queryset is user-scoped and schema-isolated, so an
    # authenticated caller is by construction a customer of THIS store.
    # Tenancy is not an extra permission here (see tenant/membership.py).
    # Writes are not customer self-service: see ``get_permissions``.
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

    def get_permissions(self):
        # An order line carries the price, the quantity and, through the
        # order total, the amount owed, and a quantity change moves stock.
        # Once an order exists that is a merchant operation, not
        # something the order's owner edits: customers read their lines
        # here and change an order only through the order endpoints.
        # DELETE maps to ``delete_orderitem``, which ADMIN/OWNER hold and
        # STAFF deliberately does not (tenant.role_scopes).
        if self.action in {"create", "update", "partial_update", "destroy"}:
            return [StoreStaffModelPermissions()]
        return super().get_permissions()

    def get_queryset(self):
        user = self.request.user

        if is_store_staff(user):
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
        # No DoesNotExist handler: DRF's generic get_object goes through
        # get_object_or_404, so a missing row is already an Http404.
        obj = super().get_object()
        self.check_order_permission(obj.order)
        return obj

    def check_order_permission(self, order):
        user = self.request.user

        if is_store_staff(user):
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
        # ``price`` is not a writable field on OrderItemWriteSerializer,
        # so the line price always comes from the product here — a
        # client cannot name its own price.
        product = serializer.validated_data["product"]
        serializer.save(price=product.price)

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
    def restock(self, request, pk=None):
        # Restocking puts returned units back on the shelf and records them
        # on the line; it moves NO money. What the customer is paid back is
        # the order's refund (``OrderService.refund_order``, the staff
        # "Refund" admin action), decided at order level because a line's
        # snapshot price is not what the customer paid for it (tax, shared
        # discounts, shipping). It is a staff/merchant operation, never
        # customer self-service (which would let an order owner restock
        # stock and mark items returned).
        if not is_store_staff(request.user):
            raise PermissionDenied(_("Only staff can restock order items."))

        order_item = self.get_object()

        if order_item.order.status not in RETURN_HANDLING_STATUSES:
            raise DRFValidationError(
                {
                    "detail": _(
                        "Units can only be restocked on a delivered, "
                        "completed or returned order."
                    )
                }
            )

        if order_item.is_refunded:
            raise DRFValidationError(
                {"detail": _("All units of this item were already restocked.")}
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
            # Restock FIRST, then record why. Writing the reason up front
            # left "Restock reason: ..." on an item whose restock then
            # failed validation, so the notes claimed a restock that never
            # happened.
            order_item.refund(quantity)

            if reason:
                notes = order_item.notes or ""
                order_item.notes = f"{notes}\nRestock reason: {reason}".strip()
                order_item.save(update_fields=["notes"])

            response_data = {
                "detail": _("The returned units were restocked."),
                "item": order_item,
            }

            response_serializer_class = self.get_response_serializer()
            response_serializer = response_serializer_class(response_data)
            return Response(response_serializer.data, status=status.HTTP_200_OK)

        except ValidationError as e:
            logger = logging.getLogger(__name__)

            logger.error(
                f"Validation error during restock: {e!s}",
                extra={
                    "order_item_id": order_item.id,
                    "user_id": request.user.id,
                },
            )
            raise DRFValidationError(
                {
                    "detail": _(
                        "A validation error occurred while restocking the item. Please check your input and try again."
                    )
                }
            ) from e
        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.error(
                f"Error restocking order item: {e!s}",
                extra={
                    "order_item_id": order_item.id,
                    "user_id": request.user.id,
                },
            )
            raise DRFValidationError(
                {
                    "detail": _(
                        "An error occurred while restocking the item. Please try again later."
                    )
                }
            ) from e
