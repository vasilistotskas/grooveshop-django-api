from __future__ import annotations

import logging
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils.translation import gettext_lazy as _
from djmoney.money import Money
from drf_spectacular.utils import extend_schema_view
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

from core.api.permissions import IsOwnerOrAdmin, IsOwnerOrAdminOrGuest
from core.api.serializers import ErrorResponseSerializer
from core.api.views import BaseModelViewSet
from core.utils.serializers import (
    ActionConfig,
    SerializersConfig,
    create_schema_view_config,
)
from core.utils.views import cache_methods
from order.exceptions import (
    InsufficientStockError,
    InvalidOrderDataError,
    InvalidStatusTransitionError,
    PaymentNotFoundError,
)
from order.filters import OrderFilter
from order.models.order import Order
from order.payment import get_payment_provider
from order.serializers.order import (
    AddTrackingSerializer,
    CancelOrderRequestSerializer,
    CreateCheckoutSessionRequestSerializer,
    CreateCheckoutSessionResponseSerializer,
    CreatePaymentIntentRequestSerializer,
    CreatePaymentIntentResponseSerializer,
    OrderCreateFromCartSerializer,
    OrderDetailSerializer,
    OrderSerializer,
    OrderWriteSerializer,
    PaymentStatusResponseSerializer,
    RefundOrderRequestSerializer,
    RefundOrderResponseSerializer,
    UpdateStatusSerializer,
)
from order.services import OrderService
from pay_way.models import PayWay
from pay_way.services import PayWayService

logger = logging.getLogger(__name__)

serializers_config: SerializersConfig = {
    "list": ActionConfig(response=OrderSerializer),
    "retrieve": ActionConfig(response=OrderDetailSerializer),
    "create": ActionConfig(
        request=OrderCreateFromCartSerializer, response=OrderDetailSerializer
    ),
    "update": ActionConfig(
        request=OrderWriteSerializer, response=OrderDetailSerializer
    ),
    "partial_update": ActionConfig(
        request=OrderWriteSerializer, response=OrderDetailSerializer
    ),
    "retrieve_by_uuid": ActionConfig(
        response=OrderDetailSerializer,
        operation_id="retrieveOrderByUuid",
        summary=_("Retrieve an order by UUID"),
        description=_(
            "Get detailed information about a specific order using its UUID"
        ),
        tags=["Orders"],
    ),
    "cancel": ActionConfig(
        request=CancelOrderRequestSerializer,
        response=OrderDetailSerializer,
        operation_id="cancelOrder",
        summary=_("Cancel an order"),
        description=_("Cancel an existing order and restore product stock"),
        tags=["Orders"],
    ),
    "my_orders": ActionConfig(
        response=OrderSerializer,
        many=True,
        operation_id="listMyOrders",
        summary=_("List current user's orders"),
        description=_("Returns a list of the authenticated user's orders"),
        tags=["Orders"],
    ),
    "add_tracking": ActionConfig(
        request=AddTrackingSerializer,
        response=OrderDetailSerializer,
        operation_id="addOrderTracking",
        summary=_("Add tracking information to an order"),
        description=_("Add tracking information to an existing order"),
        tags=["Orders"],
    ),
    "update_status": ActionConfig(
        request=UpdateStatusSerializer,
        response=OrderDetailSerializer,
        operation_id="updateOrderStatus",
        summary=_("Update the status of an order"),
        description=_("Update the status of an existing order"),
        tags=["Orders"],
    ),
    "create_payment_intent": ActionConfig(
        request=CreatePaymentIntentRequestSerializer,
        response=CreatePaymentIntentResponseSerializer,
        operation_id="createOrderPaymentIntent",
        summary=_("Create a payment intent for an order"),
        description=_(
            "Create a payment intent for Stripe payments on an existing order"
        ),
        tags=["Orders"],
    ),
    "create_checkout_session": ActionConfig(
        request=CreateCheckoutSessionRequestSerializer,
        response=CreateCheckoutSessionResponseSerializer,
        operation_id="createOrderCheckoutSession",
        summary=_("Create a Stripe Checkout Session for an order"),
        description=_(
            "Create a Stripe Checkout Session for hosted payment page. "
            "The customer will be redirected to Stripe's checkout page to complete payment."
        ),
        tags=["Orders"],
    ),
    "refund_order": ActionConfig(
        request=RefundOrderRequestSerializer,
        response=RefundOrderResponseSerializer,
        operation_id="refundOrder",
        summary=_("Refund an order payment"),
        description=_(
            "Process a full or partial refund for an order's payment. "
            "Only available for paid orders with valid payment providers."
        ),
        tags=["Orders"],
    ),
    "payment_status": ActionConfig(
        response=PaymentStatusResponseSerializer,
        operation_id="getOrderPaymentStatus",
        summary=_("Get payment status for an order"),
        description=_(
            "Retrieve the current payment status from the payment provider. "
            "This fetches the latest status directly from Stripe/PayPal."
        ),
        tags=["Orders"],
    ),
}


@extend_schema_view(
    **create_schema_view_config(
        model_class=Order,
        display_config={"tag": "Orders"},
        serializers_config=serializers_config,
        error_serializer=ErrorResponseSerializer,
    ),
)
@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class OrderViewSet(BaseModelViewSet):
    queryset = Order.objects.all()
    serializers_config = serializers_config

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
        "city",
        "street",
        "zipcode",
        "tracking_number",
        "payment_id",
    ]

    def get_queryset(self):
        """
        Return optimized queryset based on action.

        Uses Order.objects.for_list() for list views and
        Order.objects.for_detail() for detail views to avoid N+1 queries.
        """
        if self.action == "list":
            return Order.objects.for_list()
        elif self.action == "my_orders":
            return Order.objects.for_list()
        return Order.objects.for_detail()

    def get_permissions(self):
        owner_or_admin_actions = {
            "list",
            "retrieve",
            "update",
            "partial_update",
            "destroy",
            "my_orders",
        }
        guest_allowed_actions = {
            "retrieve_by_uuid",
            "cancel",
            "payment_status",
            "create_payment_intent",
            "create_checkout_session",
        }
        admin_only_actions = {"add_tracking", "update_status", "refund_order"}
        public_actions = {"create"}

        if self.action in owner_or_admin_actions:
            self.permission_classes = [IsOwnerOrAdmin]
        elif self.action in guest_allowed_actions:
            self.permission_classes = [IsOwnerOrAdminOrGuest]
        elif self.action in admin_only_actions:
            self.permission_classes = [IsAdminUser]
        elif self.action in public_actions:
            self.permission_classes = []
        else:
            self.permission_classes = [IsAdminUser]

        return super().get_permissions()

    def check_object_permissions(self, request, obj):
        if (
            request.user
            and request.user.is_authenticated
            and request.user.is_staff
        ):
            super().check_object_permissions(request, obj)
            return

        if obj.user_id is None:
            guest_allowed_actions = {
                "retrieve_by_uuid",
                "cancel",
                "payment_status",
                "create_payment_intent",
                "create_checkout_session",
            }
            if self.action not in guest_allowed_actions:
                raise PermissionDenied(
                    _("Guest orders can only be accessed via UUID.")
                )
            return

        if request.user and request.user.is_authenticated:
            if obj.user_id != request.user.id:
                raise PermissionDenied(
                    _("You do not have permission to access this order.")
                )
            super().check_object_permissions(request, obj)
            return

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
                    obj = OrderService.get_order_by_uuid(order_id)
                    self.check_object_permissions(self.request, obj)
                    return obj
                except (ValueError, TypeError):
                    pass

            obj = OrderService.get_order_by_id(int(order_id))
            self.check_object_permissions(self.request, obj)
            return obj
        except Order.DoesNotExist as e:
            raise NotFound(
                _("Order with ID {order_id} not found").format(
                    order_id=order_id
                )
            ) from e

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        """
        Create an order with dual-flow support.

        This endpoint supports two payment flows based on payment method type:

        **Online Payments (is_online_payment=True):**
        - Payment-first flow: Requires payment_intent_id
        - Flow: Payment confirmed → Order created
        - Examples: Stripe, PayPal

        **Offline Payments (is_online_payment=False):**
        - Order-first flow: No payment_intent_id required
        - Flow: Order created → Payment pending
        - Examples: Cash on Delivery, Bank Transfer

        Required request data:
        - cart_id: Cart ID or UUID to create order from
        - pay_way_id: Payment method ID
        - payment_intent_id: Required ONLY for online payments
        - Shipping address fields (first_name, last_name, email, street, etc.)

        Returns:
            Response: Order details with 201 status on success

        Raises:
            ValidationError: If validation fails or required fields missing
        """

        try:
            # Step 1: Get payment method to determine flow
            # Note: djangorestframework_camel_case converts payWay -> pay_way
            pay_way_id = request.data.get("pay_way_id")
            if not pay_way_id:
                raise ValidationError(
                    {"pay_way_id": [_("Payment method is required")]}
                )

            try:
                pay_way = PayWay.objects.get(id=pay_way_id)
            except PayWay.DoesNotExist:
                raise ValidationError(
                    {
                        "pay_way_id": [
                            _(
                                "Payment method with ID {pay_way_id} not found"
                            ).format(pay_way_id=pay_way_id)
                        ]
                    }
                )

            # Step 2: Route to appropriate flow based on payment type
            if pay_way.is_online_payment:
                # Online payment: Require payment_intent_id (payment-first)
                return self._create_with_payment_intent(request, pay_way)
            else:
                # Offline payment: No payment_intent_id required (order-first)
                return self._create_without_payment_intent(request, pay_way)

        except InsufficientStockError as e:
            logger.warning(
                "Insufficient stock for order creation: product_id=%s, available=%s, requested=%s",
                e.product_id,
                e.available,
                e.requested,
            )
            return Response(
                {
                    "detail": _("Insufficient stock for product"),
                    "error": {
                        "type": "insufficient_stock",
                        "product_id": e.product_id,
                        "available": e.available,
                        "requested": e.requested,
                    },
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        except InvalidOrderDataError as e:
            logger.warning("Invalid order data: %s", e)
            error_response = {
                "detail": str(e),
                "error": {
                    "type": "invalid_order_data",
                },
            }
            if e.field_errors:
                error_response["field_errors"] = e.field_errors
            return Response(
                error_response,
                status=status.HTTP_400_BAD_REQUEST,
            )

        except PaymentNotFoundError as e:
            logger.warning("Payment not found: %s", e)
            return Response(
                {
                    "detail": str(e),
                    "error": {
                        "type": "payment_not_found",
                        "payment_id": e.payment_id
                        if hasattr(e, "payment_id")
                        else None,
                    },
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        except ValidationError as e:
            logger.warning("Validation error: %s", e)
            # DRF ValidationError has a detail attribute
            # Return the error detail directly for proper field-level errors
            error_detail = (
                e.detail if hasattr(e, "detail") else {"detail": str(e)}
            )
            return Response(
                error_detail,
                status=status.HTTP_400_BAD_REQUEST,
            )

        except PermissionDenied as e:
            logger.warning("Permission denied: %s", e)
            return Response(
                {"detail": str(e)},
                status=status.HTTP_403_FORBIDDEN,
            )

        except Exception as e:
            logger.error(
                "Unexpected error creating order: %s", e, exc_info=True
            )
            return Response(
                {
                    "detail": _("An unexpected error occurred"),
                    "error": str(e),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _create_with_payment_intent(self, request, pay_way: PayWay) -> Response:
        """
        Create order with payment-first flow (online payments).

        Requires payment_intent_id to be provided and confirmed.
        """

        # Step 1: Get cart from request (validate cart exists first)
        cart, user = self._get_cart_and_user(request)

        # Step 2: Validate payment_intent_id is provided
        # Note: djangorestframework_camel_case converts paymentIntentId -> payment_intent_id
        payment_intent_id = request.data.get("payment_intent_id")
        if not payment_intent_id:
            raise ValidationError(
                {
                    "payment_intent_id": [
                        _(
                            "Payment intent ID is required for online payment methods"
                        )
                    ]
                }
            )

        # Step 3: Validate cart is ready for checkout
        validation_result = OrderService.validate_cart_for_checkout(cart)
        if not validation_result.get("valid", False):
            errors = validation_result.get("errors", [])
            raise ValidationError({"cart": errors})

        # Step 4: Build and validate shipping address
        shipping_address = self._build_shipping_address(request)
        try:
            OrderService.validate_shipping_address(shipping_address)
        except DjangoValidationError as e:
            # Convert Django ValidationError to DRF ValidationError
            raise ValidationError(
                e.message_dict
                if hasattr(e, "message_dict")
                else {"detail": str(e)}
            )

        # Step 5: Create order from cart with payment_intent_id
        order = OrderService.create_order_from_cart(
            cart=cart,
            shipping_address=shipping_address,
            payment_intent_id=payment_intent_id,
            pay_way=pay_way,
            user=user,
        )

        # Return order details
        response_serializer_class = self.get_response_serializer()
        response_serializer = response_serializer_class(
            order, context={"request": request}
        )

        logger.info(
            "Order %s created successfully (online payment) for user %s with payment %s",
            order.id,
            user.id if user else "guest",
            payment_intent_id,
        )

        return Response(
            response_serializer.data, status=status.HTTP_201_CREATED
        )

    def _create_without_payment_intent(
        self, request, pay_way: PayWay
    ) -> Response:
        """
        Create order with order-first flow (offline payments).

        No payment_intent_id required. Order created with PENDING status.
        """

        # Step 1: Get cart from request
        cart, user = self._get_cart_and_user(request)

        # Step 2: Validate cart is ready for checkout
        validation_result = OrderService.validate_cart_for_checkout(cart)
        if not validation_result.get("valid", False):
            errors = validation_result.get("errors", [])
            raise ValidationError({"cart": errors})

        # Step 3: Build and validate shipping address
        shipping_address = self._build_shipping_address(request)
        try:
            OrderService.validate_shipping_address(shipping_address)
        except DjangoValidationError as e:
            # Convert Django ValidationError to DRF ValidationError
            raise ValidationError(
                e.message_dict
                if hasattr(e, "message_dict")
                else {"detail": str(e)}
            )

        # Step 4: Create order from cart without payment_intent_id
        order = OrderService.create_order_from_cart_offline(
            cart=cart,
            shipping_address=shipping_address,
            pay_way=pay_way,
            user=user,
        )

        # Return order details
        response_serializer_class = self.get_response_serializer()
        response_serializer = response_serializer_class(
            order, context={"request": request}
        )

        logger.info(
            "Order %s created successfully (offline payment) for user %s",
            order.id,
            user.id if user else "guest",
        )

        return Response(
            response_serializer.data, status=status.HTTP_201_CREATED
        )

    def _get_cart_and_user(self, request):
        """
        Helper method to get cart and user from request using CartService.

        Cart is identified via X-Cart-Id header (standard approach).
        Uses CartService pattern consistent with cart views.

        Note: For order creation, we need an existing cart with items.
        We use get_existing_cart() instead of get_or_create_cart() to ensure
        the cart already exists and is not created on-the-fly.
        """
        from cart.services import CartService

        # Use CartService to get cart from X-Cart-Id header
        cart_service = CartService(request)
        cart = cart_service.get_existing_cart()

        if not cart:
            raise ValidationError(
                {
                    "cart": [
                        _(
                            "Cart not found. Please add items to cart before checkout."
                        )
                    ]
                }
            )

        # Verify cart has items
        if not cart.items.exists():
            raise ValidationError(
                {
                    "cart": [
                        _("Cart is empty. Please add items before checkout.")
                    ]
                }
            )

        # Get user (None for guest orders)
        user = request.user if request.user.is_authenticated else None

        # Verify cart ownership for authenticated users
        if user and cart.user and cart.user.id != user.id:
            raise PermissionDenied(
                _("You do not have permission to access this cart.")
            )

        return cart, user

    def _build_shipping_address(self, request) -> dict:
        """
        Helper method to build shipping address from request data.

        Note: djangorestframework_camel_case middleware automatically converts
        camelCase request data to snake_case, so we only need to check snake_case keys.
        """
        phone = request.data.get("phone")
        return {
            "first_name": request.data.get("first_name"),
            "last_name": request.data.get("last_name"),
            "email": request.data.get("email"),
            "street": request.data.get("street"),
            "street_number": request.data.get("street_number"),
            "city": request.data.get("city"),
            "zipcode": request.data.get("zipcode"),
            "country_id": request.data.get("country_id"),
            "region_id": request.data.get("region_id"),
            "phone": phone,
            "customer_notes": request.data.get("customer_notes", ""),
        }

    @action(detail=True, methods=["POST"])
    def create_payment_intent(self, request, *args, **kwargs):
        """Create a payment intent for an order."""
        order = self.get_object()

        if order.is_paid:
            return Response(
                {"detail": _("This order has already been paid.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not order.pay_way or order.pay_way.provider_code != "stripe":
            return Response(
                {
                    "detail": _(
                        "This order is not configured for Stripe payments."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        request_serializer_class = self.get_request_serializer()
        request_serializer = request_serializer_class(data=request.data)
        request_serializer.is_valid(raise_exception=True)

        validated_data = request_serializer.validated_data

        payment_data = validated_data.get("payment_data", {})

        if validated_data.get("payment_method_id"):
            payment_data["payment_method_id"] = validated_data[
                "payment_method_id"
            ]
        if validated_data.get("customer_id"):
            payment_data["customer_id"] = validated_data["customer_id"]
        if validated_data.get("return_url"):
            payment_data["return_url"] = validated_data["return_url"]

        success, payment_response = PayWayService.process_payment(
            pay_way=order.pay_way, order=order, **payment_data
        )

        if not success:
            return Response(
                {
                    "detail": _("Failed to create payment intent."),
                    "error": payment_response,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        response_serializer_class = self.get_response_serializer()
        response_serializer = response_serializer_class(data=payment_response)
        response_serializer.is_valid(raise_exception=True)

        return Response(response_serializer.validated_data)

    @action(detail=True, methods=["POST"])
    def create_checkout_session(self, request, *args, **kwargs):
        """Create a Stripe Checkout Session for an order."""
        order = self.get_object()

        if order.is_paid:
            return Response(
                {"detail": _("This order has already been paid.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not order.pay_way or order.pay_way.provider_code != "stripe":
            return Response(
                {
                    "detail": _(
                        "This order is not configured for Stripe payments."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        request_serializer_class = self.get_request_serializer()
        request_serializer = request_serializer_class(data=request.data)
        request_serializer.is_valid(raise_exception=True)

        validated_data = request_serializer.validated_data

        checkout_params = {
            "success_url": validated_data["success_url"],
            "cancel_url": validated_data["cancel_url"],
            "customer_email": validated_data.get("customer_email", order.email),
            "description": validated_data.get(
                "description", f"Payment for Order #{order.id}"
            ),
        }

        if request.user.is_authenticated:
            checkout_params["subscriber_id"] = request.user.id

        provider = get_payment_provider(order.pay_way.provider_code)

        # Pass only items total (without shipping) so Stripe can add shipping separately
        success, checkout_response = provider.create_checkout_session(
            amount=order.total_price_items,
            order_id=str(order.id),
            **checkout_params,
        )

        if not success:
            return Response(
                {
                    "detail": _("Failed to create checkout session."),
                    "error": checkout_response,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not order.metadata:
            order.metadata = {}
        order.metadata["stripe_checkout_session_id"] = checkout_response[
            "session_id"
        ]
        order.save(update_fields=["metadata"])

        response_serializer_class = self.get_response_serializer()
        response_serializer = response_serializer_class(data=checkout_response)
        response_serializer.is_valid(raise_exception=True)

        return Response(response_serializer.validated_data)

    @action(detail=True, methods=["GET"])
    def retrieve_by_uuid(self, request, *args, **kwargs):
        """Retrieve an order by UUID."""
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
        """Cancel an order and optionally process refund."""
        order = self.get_object()

        user = request.user

        is_admin = user and user.is_authenticated and user.is_staff
        is_owner = (
            user
            and user.is_authenticated
            and order.user
            and order.user.id == user.id
        )
        is_guest_order = order.user is None

        if not (is_admin or is_owner or is_guest_order):
            raise PermissionDenied(
                _("You do not have permission to cancel this order")
            )

        request_serializer_class = self.get_request_serializer()
        request_serializer = request_serializer_class(
            data=request.data if request.data else {}
        )
        request_serializer.is_valid(raise_exception=True)

        validated_data = request_serializer.validated_data
        cancellation_reason = validated_data.get("reason", "")
        should_refund = validated_data.get("refund_payment", True)

        try:
            canceled_order, refund_info = OrderService.cancel_order(
                order=order,
                reason=cancellation_reason,
                refund_payment=should_refund,
                canceled_by=user.id if user and user.is_authenticated else None,
            )

            response_serializer_class = self.get_response_serializer()
            serializer = response_serializer_class(
                canceled_order, context={"request": request}
            )
            response_data = serializer.data

            if refund_info:
                response_data["refund_info"] = refund_info

            return Response(response_data)

        except ValueError as e:
            raise ValidationError({"detail": str(e)}) from e
        except Exception as e:
            logger.error("Error canceling order: %s", e, exc_info=True)
            return Response(
                {"detail": _("An unexpected error occurred")},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=False, methods=["GET"])
    def my_orders(self, request, *args, **kwargs):
        """List current user's orders."""
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
        """Add tracking information to an order."""
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
            order = OrderService.add_tracking_info(
                order=order,
                tracking_number=tracking_number,
                shipping_carrier=shipping_carrier,
                auto_update_status=True,
            )

            response_serializer_class = self.get_response_serializer()
            response_serializer = response_serializer_class(
                order, context=self.get_serializer_context()
            )
            return Response(response_serializer.data)

        except Exception as e:
            logger.error(
                "Error adding tracking information: %s", e, exc_info=True
            )
            return Response(
                {"detail": _("An unexpected error occurred")},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=True, methods=["POST"])
    def update_status(self, request, *args, **kwargs):
        """Update the status of an order."""
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

        except InvalidStatusTransitionError as e:
            logger.error("Invalid status transition: %s", e)
            raise ValidationError(
                {
                    "detail": str(e),
                    "current_status": e.current_status,
                    "new_status": e.new_status,
                    "allowed_transitions": e.allowed,
                }
            ) from e
        except ValueError as e:
            logger.error("Error updating order status: %s", e)
            raise ValidationError(
                {
                    "detail": _(
                        "An error occurred while processing your request."
                    )
                }
            ) from e
        except Exception as e:
            logger.error("Error updating order status: %s", e, exc_info=True)
            return Response(
                {"detail": _("An unexpected error occurred")},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=True, methods=["POST"])
    def refund_order(self, request, *args, **kwargs):
        """Process full or partial refund for an order."""
        order = self.get_object()

        request_serializer_class = self.get_request_serializer()
        request_serializer = request_serializer_class(data=request.data)
        request_serializer.is_valid(raise_exception=True)

        validated_data = request_serializer.validated_data

        refund_amount = None
        if validated_data.get("amount"):
            currency = validated_data.get(
                "currency", str(order.total_price.currency)
            )
            refund_amount = Money(validated_data["amount"], currency)

        try:
            success, response_data = OrderService.refund_order(
                order=order,
                amount=refund_amount,
                reason=validated_data.get("reason", ""),
                refunded_by=request.user.id
                if request.user.is_authenticated
                else None,
            )

            if not success:
                return Response(
                    {
                        "detail": _("Failed to process refund."),
                        "error": response_data.get("error"),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            response_data["success"] = True
            response_data["message"] = _("Refund processed successfully.")

            response_serializer_class = self.get_response_serializer()
            response_serializer = response_serializer_class(data=response_data)
            response_serializer.is_valid(raise_exception=True)

            return Response(response_serializer.validated_data)

        except ValueError as e:
            return Response(
                {"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            logger.error(
                "Error processing refund for order %s: %s",
                order.id,
                e,
                exc_info=True,
            )
            return Response(
                {
                    "detail": _(
                        "An error occurred while processing the refund."
                    ),
                    "error": str(e),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=True, methods=["GET"])
    def payment_status(self, request, *args, **kwargs):
        """Get current payment status from payment provider."""
        order = self.get_object()

        try:
            payment_status_enum, status_data = OrderService.get_payment_status(
                order
            )

            response_data = {"status": payment_status_enum.value, **status_data}

            response_serializer_class = self.get_response_serializer()
            response_serializer = response_serializer_class(data=response_data)
            response_serializer.is_valid(raise_exception=True)

            return Response(response_serializer.validated_data)

        except ValueError as e:
            return Response(
                {"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            logger.error(
                "Error getting payment status for order %s: %s",
                order.id,
                e,
                exc_info=True,
            )
            return Response(
                {
                    "detail": _(
                        "An error occurred while fetching payment status."
                    ),
                    "error": str(e),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
