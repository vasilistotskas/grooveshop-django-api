from __future__ import annotations

import contextlib
import logging
import uuid

from django.conf import settings
from django.db import transaction
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import (
    NotAuthenticated,
    NotFound,
    PermissionDenied,
    ValidationError,
)

from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from core.api.permissions import IsOwnerOrAdmin
from core.api.serializers import ErrorResponseSerializer
from core.api.views import BaseModelViewSet

from core.utils.serializers import (
    create_schema_view_config,
    RequestSerializersConfig,
    ResponseSerializersConfig,
)
from core.utils.views import cache_methods
from order.enum.status import OrderStatus
from order.filters import OrderFilter
from order.models.order import Order
from order.serializers.order import (
    OrderDetailSerializer,
    OrderSerializer,
    OrderWriteSerializer,
    AddTrackingSerializer,
    UpdateStatusSerializer,
)
from order.services import OrderService, OrderServiceError

req_serializers: RequestSerializersConfig = {
    "create": OrderWriteSerializer,
    "update": OrderWriteSerializer,
    "partial_update": OrderWriteSerializer,
    "add_tracking": AddTrackingSerializer,
    "update_status": UpdateStatusSerializer,
}

res_serializers: ResponseSerializersConfig = {
    "create": OrderDetailSerializer,
    "list": OrderSerializer,
    "retrieve": OrderDetailSerializer,
    "update": OrderDetailSerializer,
    "partial_update": OrderDetailSerializer,
    "retrieve_by_uuid": OrderDetailSerializer,
    "cancel": OrderDetailSerializer,
    "my_orders": OrderSerializer,
    "add_tracking": OrderDetailSerializer,
    "update_status": OrderDetailSerializer,
}


@extend_schema_view(
    **create_schema_view_config(
        model_class=Order,
        display_config={
            "tag": "Orders",
        },
        request_serializers=req_serializers,
        response_serializers=res_serializers,
    ),
    retrieve_by_uuid=extend_schema(
        operation_id="retrieveOrderByUuid",
        summary=_("Retrieve an order by UUID"),
        description=_(
            "Get detailed information about a specific order using its UUID"
        ),
        tags=["Orders"],
        responses={
            200: OrderDetailSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    cancel=extend_schema(
        operation_id="cancelOrder",
        summary=_("Cancel an order"),
        description=_("Cancel an existing order and restore product stock"),
        tags=["Orders"],
        responses={
            200: OrderDetailSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    my_orders=extend_schema(
        operation_id="listMyOrders",
        summary=_("List current user's orders"),
        description=_("Returns a list of the authenticated user's orders"),
        tags=["Orders"],
        responses={
            200: OrderSerializer(many=True),
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    add_tracking=extend_schema(
        operation_id="addOrderTracking",
        summary=_("Add tracking information to an order"),
        description=_("Add tracking information to an existing order"),
        tags=["Orders"],
        responses={
            200: OrderDetailSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    update_status=extend_schema(
        operation_id="updateOrderStatus",
        summary=_("Update the status of an order"),
        description=_("Update the status of an existing order"),
        tags=["Orders"],
        responses={
            200: OrderDetailSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
)
@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class OrderViewSet(BaseModelViewSet):
    queryset = Order.objects.all()
    request_serializers = req_serializers
    response_serializers = res_serializers

    filterset_class = OrderFilter
    ordering_fields = [
        "id",
        "created_at",
        "updated_at",
        "status",
        "status_updated_at",
        "paid_amount",
        "shipping_price",
        "payment_status",
        "user__first_name",
        "user__last_name",
    ]
    ordering = ["-created_at"]
    search_fields = [
        "user__email",
        "user__username",
        "user__first_name",
        "user__last_name",
        "first_name",
        "last_name",
        "email",
        "phone",
        "mobile_phone",
        "city",
        "street",
        "zipcode",
        "tracking_number",
        "payment_id",
    ]
    permission_classes = [IsOwnerOrAdmin]

    def get_permissions(self):
        if self.action in [
            "list",
            "retrieve",
            "update",
            "partial_update",
            "destroy",
            "retrieve_by_uuid",
            "cancel",
            "my_orders",
        ]:
            self.permission_classes = [IsOwnerOrAdmin]
        elif self.action in ["add_tracking", "update_status"]:
            self.permission_classes = [IsAdminUser]
        elif self.action == "create":
            self.permission_classes = []
        else:
            self.permission_classes = [IsAdminUser]

        return super().get_permissions()

    def check_object_permissions(self, request, obj):
        super().check_object_permissions(request, obj)

        if request.user.is_staff:
            return

        if obj.user_id != request.user.id:
            raise PermissionDenied(
                _("You do not have permission to access this order.")
            )

    def get_object(self):
        lookup_url_kwarg = self.lookup_url_kwarg or self.lookup_field
        order_id = self.kwargs.get(lookup_url_kwarg)
        max_order_id_length = 8

        try:
            if (
                isinstance(order_id, str)
                and len(order_id) > max_order_id_length
                and "-" in order_id
            ):
                try:
                    uuid.UUID(order_id)
                    return OrderService.get_order_by_uuid(order_id)
                except (ValueError, TypeError):
                    pass

            return OrderService.get_order_by_id(int(order_id))
        except Order.DoesNotExist as e:
            raise NotFound(
                _("Order with ID {order_id} not found").format(
                    order_id=order_id
                )
            ) from e

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        request_serializer_class = self.get_request_serializer()
        request_serializer = request_serializer_class(
            data=request.data, context={"request": request}
        )

        if not request_serializer.is_valid():
            return Response(
                request_serializer.errors, status=status.HTTP_400_BAD_REQUEST
            )

        try:
            user = request.user if request.user.is_authenticated else None
            validated_data = request_serializer.validated_data.copy()

            if user and "user" not in validated_data:
                validated_data["user"] = user

            order = request_serializer.create(validated_data)

            response_serializer_class = self.get_response_serializer()
            response_serializer = response_serializer_class(
                order, context={"request": request}
            )
            return Response(
                response_serializer.data, status=status.HTTP_201_CREATED
            )

        except OrderServiceError as e:
            logger = logging.getLogger(__name__)

            logger.error(
                "Error creating order: %s",
                str(e),
            )
            raise ValidationError(
                {
                    "detail": _(
                        "An error occurred while processing your request."
                    )
                }
            ) from e
        except Exception as e:
            logger = logging.getLogger(__name__)

            logger.error(
                "Error creating order: %s",
                str(e),
            )
            return Response(
                {
                    "detail": _(
                        "An error occurred while processing your request."
                    )
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=True, methods=["GET"])
    def retrieve_by_uuid(self, request, *args, **kwargs):
        uuid_str = kwargs.get("uuid")
        if not uuid_str:
            raise NotFound(_("UUID parameter is required"))

        try:
            order = OrderService.get_order_by_uuid(uuid_str)
        except Order.DoesNotExist as e:
            raise NotFound(
                _("Order with UUID {uuid} not found").format(uuid=uuid_str)
            ) from e

        self.check_object_permissions(request, order)
        response_serializer_class = self.get_response_serializer()
        serializer = response_serializer_class(order)
        return Response(serializer.data)

    @action(detail=True, methods=["POST"])
    def cancel(self, request, *args, **kwargs):
        order = self.get_object()

        user = request.user
        if not user.is_staff and (not order.user or order.user.id != user.id):
            raise PermissionDenied(
                _("You do not have permission to cancel this order")
            )

        try:
            canceled_order = OrderService.cancel_order(order)
            response_serializer_class = self.get_response_serializer()
            serializer = response_serializer_class(canceled_order)
            return Response(serializer.data)
        except OrderServiceError as e:
            logger = logging.getLogger(__name__)

            logger.error(f"Error canceling order: {e}")
            raise ValidationError(
                {
                    "detail": _(
                        "An error occurred while processing your request."
                    )
                }
            ) from e
        except Exception as e:
            logger = logging.getLogger(__name__)

            logger.error(
                "Error canceling order: %s",
                str(e),
            )

            return Response(
                {"detail": _("An unexpected error occurred")},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=False, methods=["GET"])
    def my_orders(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            raise NotAuthenticated(
                _("Authentication is required to view your orders")
            )

        user_orders = OrderService.get_user_orders(request.user.id)

        filtered_qs = self.filter_queryset(user_orders)

        page = self.paginate_queryset(filtered_qs)
        response_serializer_class = self.get_response_serializer()
        if page is not None:
            return self.paginate_and_serialize(
                page, request, serializer_class=response_serializer_class
            )

        serializer = response_serializer_class(filtered_qs, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["POST"])
    def add_tracking(self, request, *args, **kwargs):
        order = self.get_object()

        if not request.user.is_staff:
            raise PermissionDenied(_("Only staff can add tracking information"))

        request_serializer_class = self.get_request_serializer()
        request_serializer = request_serializer_class(data=request.data)
        request_serializer.is_valid(raise_exception=True)

        validated_data = request_serializer.validated_data
        tracking_number = validated_data["tracking_number"]
        shipping_carrier = validated_data["shipping_carrier"]

        try:
            order.add_tracking_info(tracking_number, shipping_carrier)

            if (
                order.status
                in {
                    OrderStatus.DELIVERED,
                    OrderStatus.COMPLETED,
                    OrderStatus.RETURNED,
                    OrderStatus.REFUNDED,
                }
                or order.status == OrderStatus.SHIPPED
            ):
                pass
            elif order.status == OrderStatus.PROCESSING:
                OrderService.update_order_status(order, OrderStatus.SHIPPED)
            elif order.status == OrderStatus.PENDING:
                OrderService.update_order_status(order, OrderStatus.PROCESSING)
                OrderService.update_order_status(order, OrderStatus.SHIPPED)
            else:
                with contextlib.suppress(OrderServiceError):
                    OrderService.update_order_status(order, OrderStatus.SHIPPED)

            order = OrderService.get_order_by_id(order.id)

            response_serializer_class = self.get_response_serializer()
            response_serializer = response_serializer_class(
                order, context=self.get_serializer_context()
            )
            return Response(response_serializer.data)

        except OrderServiceError as e:
            logger = logging.getLogger(__name__)

            logger.error(f"Error adding tracking information: {e}")
            raise ValidationError(
                {
                    "detail": _(
                        "An error occurred while processing your request."
                    )
                }
            ) from e
        except Exception as e:
            logger = logging.getLogger(__name__)

            logger.error(
                "Error adding tracking information: %s",
                str(e),
            )
            return Response(
                {"detail": _("An unexpected error occurred")},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=True, methods=["POST"])
    def update_status(self, request, *args, **kwargs):
        order = self.get_object()

        if not request.user.is_staff:
            raise PermissionDenied(_("Only staff can update order status"))

        request_serializer_class = self.get_request_serializer()
        request_serializer = request_serializer_class(data=request.data)
        request_serializer.is_valid(raise_exception=True)

        validated_data = request_serializer.validated_data
        status_value = validated_data["status"]

        try:
            OrderService.update_order_status(order, status_value)

            order = OrderService.get_order_by_id(order.id)

            response_serializer_class = self.get_response_serializer()
            response_serializer = response_serializer_class(
                order, context=self.get_serializer_context()
            )
            return Response(response_serializer.data)

        except ValueError as e:
            logger = logging.getLogger(__name__)

            logger.error(f"Error updating order status: {e}")
            raise ValidationError(
                {
                    "detail": _(
                        "An error occurred while processing your request."
                    )
                }
            ) from e
        except OrderServiceError as e:
            logger = logging.getLogger(__name__)

            logger.error(f"Error updating order status: {e}")
            raise ValidationError(
                {
                    "detail": _(
                        "An error occurred while processing your request."
                    )
                }
            ) from e
        except Exception as e:
            logger = logging.getLogger(__name__)

            logger.error(
                "Error updating order status: %s",
                str(e),
            )
            return Response(
                {"detail": _("An unexpected error occurred")},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
