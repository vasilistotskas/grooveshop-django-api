from __future__ import annotations

import logging

from django.utils.translation import gettext_lazy as _
from drf_spectacular.openapi import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema_view,
)
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAdminUser

from rest_framework.response import Response

from cart.filters.cart import CartFilter
from cart.models import Cart
from cart.serializers.cart import (
    CartDetailSerializer,
    CartSerializer,
    CartWriteSerializer,
    ReleaseReservationsRequestSerializer,
    ReleaseReservationsResponseSerializer,
    ReserveStockResponseSerializer,
)
from cart.services import CartService
from core.api.serializers import ErrorResponseSerializer
from core.api.views import BaseModelViewSet

from core.utils.serializers import (
    ActionConfig,
    SerializersConfig,
    create_schema_view_config,
)
from order.exceptions import InsufficientStockError, StockReservationError
from order.stock import StockManager

logger = logging.getLogger(__name__)

GUEST_CART_HEADERS = [
    OpenApiParameter(
        name="X-Cart-Id",
        type=OpenApiTypes.INT,
        location=OpenApiParameter.HEADER,
        description="Cart ID for guest users. Used to identify and maintain guest cart sessions.",
        required=False,
    ),
]

serializers_config: SerializersConfig = {
    "list": ActionConfig(
        response=CartSerializer,
        many=True,
        operation_id="listCart",
        summary=_("Get cart"),
        description=_(
            "Get a cart. For guest users, include X-Cart-Id header to maintain cart session."
        ),
        tags=["Cart"],
        parameters=GUEST_CART_HEADERS,
    ),
    "retrieve": ActionConfig(
        response=CartDetailSerializer,
        operation_id="retrieveCart",
        summary=_("Get cart"),
        description=_(
            "Get a cart. For guest users, include X-Cart-Id header to maintain cart session."
        ),
        tags=["Cart"],
        parameters=GUEST_CART_HEADERS,
    ),
    "update": ActionConfig(
        request=CartWriteSerializer,
        response=CartDetailSerializer,
        operation_id="updateCart",
        summary=_("Update cart"),
        description=_(
            "Update a cart. For guest users, include X-Cart-Id header to maintain cart session."
        ),
        tags=["Cart"],
        parameters=GUEST_CART_HEADERS,
    ),
    "partial_update": ActionConfig(
        request=CartWriteSerializer,
        response=CartDetailSerializer,
        operation_id="partialUpdateCart",
        summary=_("Update cart"),
        description=_(
            "Update a cart. For guest users, include X-Cart-Id header to maintain cart session."
        ),
        tags=["Cart"],
        parameters=GUEST_CART_HEADERS,
    ),
    "destroy": ActionConfig(
        operation_id="destroyCart",
        summary=_("Delete cart"),
        description=_(
            "Delete a cart. For guest users, include X-Cart-Id header to maintain cart session."
        ),
        tags=["Cart"],
        parameters=GUEST_CART_HEADERS,
    ),
    "create": ActionConfig(
        operation_id="createCart",
        summary=_("Create cart"),
        description=_("Cart creation is not allowed via API."),
        tags=["Cart"],
        responses={405: ErrorResponseSerializer},
    ),
    "reserve_stock": ActionConfig(
        response=ReserveStockResponseSerializer,
        operation_id="reserveCartStock",
        summary=_("Reserve stock for cart items"),
        description=_(
            "Reserve stock for all items in the cart during checkout. "
            "Creates temporary stock reservations with 15-minute TTL. "
            "Returns list of reservation IDs to be used during order creation."
        ),
        tags=["Cart"],
        parameters=GUEST_CART_HEADERS,
    ),
    "release_reservations": ActionConfig(
        request=ReleaseReservationsRequestSerializer,
        response=ReleaseReservationsResponseSerializer,
        operation_id="releaseCartReservations",
        summary=_("Release stock reservations"),
        description=_(
            "Release stock reservations when checkout is abandoned or payment fails. "
            "This makes the reserved stock available for other customers."
        ),
        tags=["Cart"],
        parameters=GUEST_CART_HEADERS,
    ),
    "create_payment_intent": ActionConfig(
        operation_id="createCartPaymentIntent",
        summary=_("Create payment intent from cart"),
        description=_(
            "Create a Stripe payment intent based on cart total before order creation. "
            "This is required for online payment methods (Stripe) in the payment-first flow. "
            "Returns client_secret for frontend payment confirmation and payment_intent_id "
            "to be included in order creation request."
        ),
        tags=["Cart"],
        parameters=GUEST_CART_HEADERS,
    ),
}


@extend_schema_view(
    **create_schema_view_config(
        model_class=Cart,
        serializers_config=serializers_config,
        error_serializer=ErrorResponseSerializer,
        display_config={
            "tag": "Cart",
            "display_name": _("cart"),
            "display_name_plural": _("carts"),
        },
    )
)
class CartViewSet(BaseModelViewSet):
    cart_service: CartService
    queryset = Cart.objects.all()
    serializers_config = serializers_config
    filterset_class = CartFilter
    ordering_fields = [
        "id",
        "user",
        "created_at",
        "updated_at",
        "last_activity",
    ]
    ordering = ["-last_activity", "-created_at"]
    search_fields = ["user__email", "user__first_name", "user__last_name"]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self.cart_service = CartService(request=request)

    def get_permissions(self):
        if self.action in [
            "list",
        ]:
            self.permission_classes = [IsAdminUser]
        return super().get_permissions()

    def get_queryset(self):
        """
        Return optimized queryset based on user permissions.

        Uses Cart.objects.for_detail() for optimized queries.
        """
        user = self.request.user

        if user.is_staff:
            return Cart.objects.for_list()
        elif user.is_authenticated:
            return Cart.objects.for_detail().filter(user=user)
        elif self.cart_service.cart:
            return Cart.objects.for_detail().filter(
                id=self.cart_service.cart.id
            )

        return Cart.objects.none()

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["cart"] = self.cart_service.cart
        return context

    def create(self, request, *args, **kwargs):
        return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)

    def retrieve(self, request, *args, **kwargs):
        cart = self.cart_service.get_or_create_cart()
        if not cart:
            return Response(status=status.HTTP_404_NOT_FOUND)
        response_serializer_class = self.get_response_serializer()
        response_serializer = response_serializer_class(cart)
        return Response(response_serializer.data, status=status.HTTP_200_OK)

    def update(self, request, *args, **kwargs):
        cart = self.cart_service.get_or_create_cart()
        if not cart:
            return Response(status=status.HTTP_404_NOT_FOUND)
        request_serializer_class = self.get_request_serializer()
        request_serializer = request_serializer_class(
            cart, data=request.data, partial=kwargs.pop("partial", False)
        )
        request_serializer.is_valid(raise_exception=True)
        self.perform_update(request_serializer)

        response_serializer_class = self.get_response_serializer()
        response_serializer = response_serializer_class(
            cart, context=self.get_serializer_context()
        )
        return Response(response_serializer.data)

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        cart = self.cart_service.get_or_create_cart()
        if cart:
            cart.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(status=status.HTTP_404_NOT_FOUND)

    @action(detail=False, methods=["post"], url_path="reserve-stock")
    def reserve_stock(self, request, *args, **kwargs):
        """
        Reserve stock for all cart items during checkout.

        This endpoint is called when the customer begins the checkout process.
        It creates temporary stock reservations (15-minute TTL) for all items
        in the cart to prevent other customers from purchasing the same items
        while this customer completes payment.

        The reservation IDs returned should be stored by the frontend and used
        during order creation to convert reservations to permanent stock decrements.
        """
        # Get the cart for the current user or guest
        cart = self.cart_service.get_or_create_cart()
        if not cart:
            return Response(
                {"detail": "Cart not found"}, status=status.HTTP_404_NOT_FOUND
            )

        # Get cart items with product information
        cart_items = cart.get_items()

        if not cart_items:
            return Response(
                {
                    "detail": "Cart is empty. Cannot reserve stock for empty cart."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Reserve stock for each cart item
        reservation_ids = []
        failed_items = []

        for item in cart_items:
            try:
                # Reserve stock using StockManager
                # session_id is the cart's UUID for tracking
                # user_id is None for guest users
                reservation = StockManager.reserve_stock(
                    product_id=item.product.id,
                    quantity=item.quantity,
                    session_id=str(cart.uuid),
                    user_id=cart.user.id if cart.user else None,
                )
                reservation_ids.append(reservation.id)
            except InsufficientStockError as e:
                # Track which items failed to reserve
                failed_items.append(
                    {
                        "product_id": e.product_id,
                        "product_name": item.product.safe_translation_getter(
                            "name", any_language=True
                        ),
                        "available": e.available,
                        "requested": e.requested,
                    }
                )

        # If any items failed to reserve, release all successful reservations
        # and return error
        if failed_items:
            # Release all successfully created reservations
            for reservation_id in reservation_ids:
                try:
                    StockManager.release_reservation(reservation_id)
                except StockReservationError:
                    # Log but don't fail if release fails
                    pass

            return Response(
                {
                    "detail": "Insufficient stock for one or more items",
                    "failed_items": failed_items,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Return success with reservation IDs
        return Response(
            {
                "reservation_ids": reservation_ids,
                "message": f"Successfully reserved stock for {len(reservation_ids)} items",
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="release-reservations")
    def release_reservations(self, request, *args, **kwargs):
        """
        Release stock reservations.

        This endpoint is called when:
        - Customer abandons checkout
        - Payment fails
        - Customer navigates away from checkout

        It releases the temporary stock reservations, making the stock
        available for other customers to purchase.
        """
        # Get reservation_ids from request data
        reservation_ids = request.data.get("reservation_ids", [])

        if not reservation_ids:
            return Response(
                {"detail": "reservation_ids is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not isinstance(reservation_ids, list):
            return Response(
                {"detail": "reservation_ids must be a list"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Release each reservation
        released_count = 0
        failed_releases = []

        for reservation_id in reservation_ids:
            try:
                StockManager.release_reservation(reservation_id)
                released_count += 1
            except StockReservationError as e:
                # Track failed releases but continue processing others
                failed_releases.append(
                    {
                        "reservation_id": reservation_id,
                        "error": str(e),
                    }
                )

        # Return success even if some releases failed
        # (they may have already been released or expired)
        response_data = {
            "message": f"Released {released_count} of {len(reservation_ids)} reservations",
            "released_count": released_count,
        }

        if failed_releases:
            response_data["failed_releases"] = failed_releases

        return Response(response_data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], url_path="create-payment-intent")
    def create_payment_intent(self, request, *args, **kwargs):
        """
        Create a Stripe payment intent from cart before order creation.

        This endpoint is called during checkout for online payment methods (Stripe).
        It creates a payment intent based on the cart total, which must be confirmed
        before the order can be created.

        Flow:
        1. Get cart and validate it has items
        2. Get payment method and validate it's Stripe
        3. Calculate cart total (items + shipping + fees)
        4. Create Stripe payment intent
        5. Return client_secret and payment_intent_id

        The payment_intent_id must be included in the order creation request.
        """
        from pay_way.models import PayWay
        from pay_way.services import PayWayService

        cart = self.cart_service.get_or_create_cart()
        if not cart:
            logger.error("Cart not found")
            return Response(
                {"detail": "Cart not found"}, status=status.HTTP_404_NOT_FOUND
            )

        if not cart.items.exists():
            logger.error("Cart is empty")
            return Response(
                {
                    "detail": "Cart is empty. Cannot create payment intent for empty cart."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Get and validate payment method
        pay_way_id = request.data.get("pay_way_id")
        if not pay_way_id:
            logger.error("pay_way_id is missing from request")
            return Response(
                {"detail": "pay_way_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            pay_way = PayWay.objects.get(id=pay_way_id)
            logger.info(
                f"Payment method found: {pay_way.name} (provider: {pay_way.provider_code}, is_online: {pay_way.is_online_payment})"
            )
        except PayWay.DoesNotExist:
            logger.error(f"Payment method with ID {pay_way_id} not found")
            return Response(
                {"detail": f"Payment method with ID {pay_way_id} not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Validate it's an online payment method (Stripe)
        if not pay_way.is_online_payment or pay_way.provider_code != "stripe":
            logger.error(
                f"Invalid payment method: is_online={pay_way.is_online_payment}, provider={pay_way.provider_code}"
            )
            return Response(
                {
                    "detail": "This endpoint only supports Stripe payment methods"
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Calculate cart total (this will include shipping and fees when order is created)
        # For now, use cart total as base amount
        cart_total = cart.total_price

        # Get Stripe payment provider
        provider = PayWayService.get_provider_for_pay_way(pay_way)
        if not provider:
            logger.error("Stripe payment provider not available")
            return Response(
                {"detail": "Stripe payment provider not available"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Create payment intent
        try:
            success, payment_data = provider.process_payment(
                amount=cart_total,
                order_id=f"cart_{cart.uuid}",  # Temporary ID until order is created
                metadata={
                    "cart_id": str(cart.uuid),
                    "cart_total": str(cart_total.amount),
                    "currency": cart_total.currency,
                },
            )

            logger.info(
                f"Payment intent creation result: success={success}, payment_data={payment_data}"
            )

            if not success:
                logger.error(
                    f"Failed to create payment intent: {payment_data.get('error', 'Unknown error')}"
                )
                return Response(
                    {
                        "detail": "Failed to create payment intent",
                        "error": payment_data.get("error", "Unknown error"),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Extract and convert values to ensure JSON serialization
            # CRITICAL: Convert Currency object to string code BEFORE building response dict
            # The Currency object from django-money is not JSON serializable
            currency_code = (
                cart_total.currency.code
                if hasattr(cart_total.currency, "code")
                else str(cart_total.currency)
            )

            client_secret = str(payment_data.get("client_secret", ""))
            payment_intent_id = str(payment_data.get("payment_id", ""))
            amount = str(cart_total.amount)

            # Return client_secret and payment_intent_id
            response_data = {
                "client_secret": client_secret,
                "payment_intent_id": payment_intent_id,
                "amount": amount,
                "currency": currency_code,
            }
            return Response(
                response_data,
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            logger.error(
                f"Error creating payment intent from cart: {e}", exc_info=True
            )
            return Response(
                {"detail": "An error occurred while creating payment intent"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
