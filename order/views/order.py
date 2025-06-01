import contextlib
import uuid

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils.translation import gettext_lazy as _
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import (
    NotAuthenticated,
    NotFound,
    PermissionDenied,
    ValidationError,
)
from rest_framework.filters import SearchFilter
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response

from core.api.permissions import IsSelfOrAdmin
from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.serializers import MultiSerializerMixin
from order.enum.status_enum import OrderStatusEnum
from order.models.order import Order
from order.serializers.order import (
    OrderCreateUpdateSerializer,
    OrderDetailSerializer,
    OrderSerializer,
)
from order.services import OrderService, OrderServiceError

User = get_user_model()


@extend_schema_view(
    list=extend_schema(
        summary=_("List all orders"),
        description=_("Returns a list of all orders with pagination"),
        tags=["Order"],
        responses={200: OrderSerializer(many=True)},
    ),
    create=extend_schema(
        summary=_("Create an order or process a checkout"),
        description=_("Process a checkout and create a new order"),
        tags=["Order"],
        request=OrderCreateUpdateSerializer,
        responses={201: OrderDetailSerializer},
    ),
    update=extend_schema(
        summary=_("Update an order"),
        description=_("Update an existing order"),
        tags=["Order"],
        request=OrderCreateUpdateSerializer,
        responses={200: OrderDetailSerializer},
    ),
    partial_update=extend_schema(
        summary=_("Partially update an order"),
        description=_("Partially update an existing order"),
        tags=["Order"],
        request=OrderCreateUpdateSerializer,
        responses={200: OrderDetailSerializer},
    ),
    retrieve=extend_schema(
        summary=_("Retrieve an order by ID"),
        description=_("Get detailed information about a specific order"),
        tags=["Order"],
        responses={200: OrderDetailSerializer},
    ),
    retrieve_by_uuid=extend_schema(
        summary=_("Retrieve an order by UUID"),
        description=_(
            "Get detailed information about a specific order using its UUID"
        ),
        tags=["Order"],
        responses={200: OrderDetailSerializer},
    ),
    destroy=extend_schema(
        summary=_("Delete an order"),
        description=_("Delete an existing order and restore product stock"),
        tags=["Order"],
        responses={204: None},
    ),
    cancel=extend_schema(
        summary=_("Cancel an order"),
        description=_("Cancel an existing order and restore product stock"),
        tags=["Order"],
        responses={200: OrderDetailSerializer},
    ),
    my_orders=extend_schema(
        summary=_("List current user's orders"),
        description=_("Returns a list of the authenticated user's orders"),
        tags=["Order"],
        responses={200: OrderSerializer(many=True)},
    ),
    add_tracking=extend_schema(
        summary=_("Add tracking information to an order"),
        description=_("Add tracking information to an existing order"),
        tags=["Order"],
        responses={200: OrderDetailSerializer},
    ),
    update_status=extend_schema(
        summary=_("Update the status of an order"),
        description=_("Update the status of an existing order"),
        tags=["Order"],
        responses={200: OrderDetailSerializer},
    ),
)
class OrderViewSet(MultiSerializerMixin, BaseModelViewSet):
    queryset = Order.objects.all()
    filter_backends = [
        DjangoFilterBackend,
        PascalSnakeCaseOrderingFilter,
        SearchFilter,
    ]
    ordering_fields = ["created_at", "status", "paid_amount"]
    filterset_fields = ["user_id", "status", "payment_status"]
    ordering = ["-created_at"]
    search_fields = [
        "user__email",
        "user__username",
        "user_id",
        "first_name",
        "last_name",
        "email",
        "phone",
    ]
    permission_classes = [IsAuthenticated, IsSelfOrAdmin]

    serializers = {
        "default": OrderSerializer,
        "create": OrderCreateUpdateSerializer,
        "update": OrderCreateUpdateSerializer,
        "partial_update": OrderCreateUpdateSerializer,
        "retrieve": OrderDetailSerializer,
        "retrieve_by_uuid": OrderDetailSerializer,
        "cancel": OrderDetailSerializer,
        "add_tracking": OrderDetailSerializer,
        "update_status": OrderDetailSerializer,
    }

    def get_permissions(self):
        if self.action in [
            "list",
            "retrieve",
            "retrieve_by_uuid",
            "update",
            "partial_update",
            "destroy",
        ]:
            self.permission_classes = [IsSelfOrAdmin]
        elif self.action in ["add_tracking", "update_status"]:
            self.permission_classes = [IsAdminUser]
        elif self.action == "create":
            self.permission_classes = []
        else:
            self.permission_classes = [IsAuthenticated]

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
        serializer = self.get_serializer(
            data=request.data, context={"request": request}
        )

        if not serializer.is_valid():
            return Response(
                serializer.errors, status=status.HTTP_400_BAD_REQUEST
            )

        try:
            user = request.user if request.user.is_authenticated else None
            validated_data = serializer.validated_data.copy()

            if user and "user" not in validated_data:
                validated_data["user"] = user

            order = serializer.create(validated_data)

            result_serializer = OrderDetailSerializer(
                order, context={"request": request}
            )
            return Response(
                result_serializer.data, status=status.HTTP_201_CREATED
            )

        except OrderServiceError as e:
            import logging

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
            import logging

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
        serializer = self.get_serializer(order)
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
            serializer = self.get_serializer(canceled_order)
            return Response(serializer.data)
        except OrderServiceError as e:
            import logging

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
            import logging

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
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(filtered_qs, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["POST"])
    def add_tracking(self, request, *args, **kwargs):
        order = self.get_object()

        if not request.user.is_staff:
            raise PermissionDenied(_("Only staff can add tracking information"))

        tracking_number = request.data.get("tracking_number")
        shipping_carrier = request.data.get("shipping_carrier")

        if not tracking_number:
            raise ValidationError(
                {"tracking_number": _("Tracking number is required")}
            )

        if not shipping_carrier:
            raise ValidationError(
                {"shipping_carrier": _("Shipping carrier is required")}
            )

        try:
            order.add_tracking_info(tracking_number, shipping_carrier)

            if (
                order.status
                in {
                    OrderStatusEnum.DELIVERED,
                    OrderStatusEnum.COMPLETED,
                    OrderStatusEnum.RETURNED,
                    OrderStatusEnum.REFUNDED,
                }
                or order.status == OrderStatusEnum.SHIPPED
            ):
                pass
            elif order.status == OrderStatusEnum.PROCESSING:
                OrderService.update_order_status(order, OrderStatusEnum.SHIPPED)
            elif order.status == OrderStatusEnum.PENDING:
                OrderService.update_order_status(
                    order, OrderStatusEnum.PROCESSING
                )
                OrderService.update_order_status(order, OrderStatusEnum.SHIPPED)
            else:
                with contextlib.suppress(OrderServiceError):
                    OrderService.update_order_status(
                        order, OrderStatusEnum.SHIPPED
                    )

            order = OrderService.get_order_by_id(order.id)
            serializer = self.get_serializer(order)
            return Response(serializer.data)

        except OrderServiceError as e:
            import logging

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
            import logging

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

        status_value = request.data.get("status")
        if not status_value:
            raise ValidationError({"status": _("Status is required")})

        try:
            OrderService.update_order_status(order, status_value)

            order = OrderService.get_order_by_id(order.id)

            serializer = self.get_serializer(order)
            return Response(serializer.data)
        except ValueError as e:
            import logging

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
            import logging

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
            import logging

            logger = logging.getLogger(__name__)

            logger.error(
                "Error updating order status: %s",
                str(e),
            )
            return Response(
                {"detail": _("An unexpected error occurred")},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
