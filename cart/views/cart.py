from __future__ import annotations

import logging

from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from djmoney.money import Money
from drf_spectacular.openapi import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema_view,
)
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny

from rest_framework.response import Response

from core.api.permissions import StoreStaffModelPermissions
from cart.filters.cart import CartFilter
from cart.models import Cart
from cart.serializers.cart import (
    CartCreatePaymentIntentRequestSerializer,
    CartDetailSerializer,
    CartPaymentIntentResponseSerializer,
    CartSerializer,
    CartWriteSerializer,
    CouponApplyRequestSerializer,
    CouponErrorResponseSerializer,
    ReleaseReservationsRequestSerializer,
    ReleaseReservationsResponseSerializer,
    ReserveStockResponseSerializer,
)
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle

from cart.services import CartService
from core.api.serializers import ErrorResponseSerializer
from core.api.throttling import (
    CartMutationAnonThrottle,
    CartMutationThrottle,
    CouponApplyThrottle,
)
from tenant.permissions import IsPromotionsEnabled
from core.api.views import BaseModelViewSet

from core.utils.serializers import (
    ActionConfig,
    SerializersConfig,
    create_schema_view_config,
)
from order.exceptions import InsufficientStockError, StockReservationError
from order.models import StockReservation
from order.services import OrderService
from order.stock import StockManager
from tenant.membership import is_store_staff

logger = logging.getLogger(__name__)

GUEST_CART_HEADERS = [
    OpenApiParameter(
        name="X-Cart-Id",
        type=OpenApiTypes.UUID,
        location=OpenApiParameter.HEADER,
        description=(
            "Cart UUID for guest users. Used to identify and maintain "
            "guest cart sessions. Sequential integer IDs were enumerable "
            "metadata, so the public identifier is the UUID inherited "
            "from ``UUIDModel`` (M18 in MULTI_TENANT_AUDIT.md)."
        ),
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
        request=CartCreatePaymentIntentRequestSerializer,
        response=CartPaymentIntentResponseSerializer,
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
    "apply_coupon": ActionConfig(
        request=CouponApplyRequestSerializer,
        response=CartDetailSerializer,
        responses={400: CouponErrorResponseSerializer},
        operation_id="applyCartCoupon",
        summary=_("Apply a coupon code to the cart"),
        description=_(
            "Attach a coupon code to the cart. Returns the updated cart "
            "with promotion discount fields. Rejections carry a "
            "machine-readable reason."
        ),
        tags=["Cart"],
        parameters=GUEST_CART_HEADERS,
    ),
    "remove_coupon": ActionConfig(
        response=CartDetailSerializer,
        operation_id="removeCartCoupon",
        summary=_("Remove the applied coupon code from the cart"),
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

    _MUTATION_ACTIONS = frozenset(
        {
            "update",
            "partial_update",
            "destroy",
            "reserve_stock",
            "release_reservations",
            "create_payment_intent",
            "apply_coupon",
            "remove_coupon",
        }
    )

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self.cart_service = CartService(request=request)

    def get_permissions(self):
        if self.action == "list":
            self.permission_classes = [StoreStaffModelPermissions]
        elif self.action in {"apply_coupon", "remove_coupon"}:
            # Guest-capable like the rest of the cart surface, but 404s
            # when the tenant's promotions plan flag is off.
            self.permission_classes = [IsPromotionsEnabled]
        else:
            # All other cart actions (retrieve, update, destroy, reserve_stock,
            # release_reservations, create_payment_intent) support guest users
            # via X-Cart-Id header — anonymous requests must be permitted.
            self.permission_classes = [AllowAny]
        return super().get_permissions()

    def get_throttles(self):
        if self.action == "apply_coupon":
            # Coupon apply is a brute-forceable code oracle — cap it far
            # tighter than the generic cart-mutation burst limits.
            return [
                CouponApplyThrottle(),
                CartMutationThrottle(),
                CartMutationAnonThrottle(),
                AnonRateThrottle(),
                UserRateThrottle(),
            ]
        if self.action in self._MUTATION_ACTIONS:
            # Per-minute burst limits layered ON TOP of the global daily caps
            # (DEFAULT_THROTTLE_CLASSES) — include the defaults explicitly so
            # the override supplements rather than replaces them.
            return [
                CartMutationThrottle(),
                CartMutationAnonThrottle(),
                AnonRateThrottle(),
                UserRateThrottle(),
            ]
        return super().get_throttles()

    def get_queryset(self):
        """
        Return optimized queryset based on user permissions.

        Uses Cart.objects.for_detail() for optimized queries.
        """
        user = self.request.user

        if is_store_staff(user):
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

    @staticmethod
    def _carry_b2b_pricing(source, target):
        """Transplant the wholesale-pricing context onto a freshly
        materialized instance of the SAME cart row.

        The ``for_detail()`` reloads below exist for prefetching — but a
        fresh instance silently falls back to retail prices, which is
        exactly the preview≠charge desync the binding design prevents.
        Same request, same user, same row → the context is reused as-is
        (no extra queries).
        """
        context = getattr(source, "_b2b_pricing", None)
        if context is not None and source.pk == target.pk:
            target._b2b_pricing = context
            # The items prefetch routes through CartItem.objects
            # .for_list(), whose select_related("cart") hands every
            # line its OWN (unbound) cart instance — re-point them at
            # the bound target or the serializer prices lines at
            # retail while the cart totals say wholesale.
            prefetched = getattr(target, "_prefetched_objects_cache", {})
            for item in prefetched.get("items", []):
                item.cart = target
        return target

    def retrieve(self, request, *args, **kwargs):
        cart = self.cart_service.get_or_create_cart()
        if not cart:
            return Response(status=status.HTTP_404_NOT_FOUND)
        # Re-load through for_detail() so the items are prefetched and the
        # totals are annotated: the cart_service returns a bare row whose
        # total_* properties would otherwise re-run get_items() several
        # times and the nested items serializer would N+1 per line (G0081).
        cart = self._carry_b2b_pricing(
            cart, Cart.objects.for_detail().get(pk=cart.pk)
        )
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

        # Re-load optimized so the response serialization reads prefetched
        # items + annotated totals rather than re-querying per property/line
        # (G0081).
        cart = self._carry_b2b_pricing(
            cart, Cart.objects.for_detail().get(pk=cart.pk)
        )
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

    def _cart_detail_response(self, cart, status_code=status.HTTP_200_OK):
        """Serialize the cart the same way ``retrieve`` does (optimized
        reload so items are prefetched and totals annotated)."""
        cart = self._carry_b2b_pricing(
            cart, Cart.objects.for_detail().get(pk=cart.pk)
        )
        response_serializer = CartDetailSerializer(
            cart, context=self.get_serializer_context()
        )
        return Response(response_serializer.data, status=status_code)

    @action(detail=False, methods=["post"], url_path="coupon")
    def apply_coupon(self, request, *args, **kwargs):
        """Attach a coupon code to the cart and return the updated cart."""
        from promotion.services import CouponError, CouponService

        cart = self.cart_service.get_or_create_cart()
        if not cart:
            return Response(
                {"detail": _("Cart not found")},
                status=status.HTTP_404_NOT_FOUND,
            )

        request_serializer_class = self.get_request_serializer()
        request_serializer = request_serializer_class(data=request.data)
        request_serializer.is_valid(raise_exception=True)

        try:
            CouponService.apply(
                cart,
                request_serializer.validated_data["code"],
                user=request.user if request.user.is_authenticated else None,
            )
        except CouponError as exc:
            return Response(
                {"detail": exc.message, "reason": exc.reason},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return self._cart_detail_response(cart)

    @action(detail=False, methods=["delete"], url_path="coupon")
    def remove_coupon(self, request, *args, **kwargs):
        """Detach the applied coupon code from the cart."""
        from promotion.services import CouponService

        cart = self.cart_service.get_or_create_cart()
        if not cart:
            return Response(
                {"detail": _("Cart not found")},
                status=status.HTTP_404_NOT_FOUND,
            )
        CouponService.remove(cart)
        return self._cart_detail_response(cart)

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
                # Track which items failed to reserve — and log it, or the
                # resulting 409 is unanswerable from server logs alone.
                logger.info(
                    "Stock reservation rejected: cart=%s product=%s "
                    "available=%s requested=%s",
                    cart.uuid,
                    e.product_id,
                    e.available,
                    e.requested,
                )
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
                except StockReservationError as release_error:
                    # Don't fail the rollback, but leave a trace — a stuck
                    # reservation holds stock until its TTL expiry.
                    logger.warning(
                        "Failed to release reservation %s during "
                        "reserve_stock rollback for cart %s: %s",
                        reservation_id,
                        cart.uuid,
                        release_error,
                    )

            return Response(
                {
                    "detail": "Insufficient stock for one or more items",
                    "failed_items": failed_items,
                },
                status=status.HTTP_409_CONFLICT,
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

        # Ownership gate (C11 in MULTI_TENANT_AUDIT.md): a reservation
        # belongs to the requester if it was made by their user account
        # OR its session_id matches the current cart's UUID. The ids come
        # straight from the request body, so without this any caller could
        # enumerate integer ids and free other customers' holds (IDOR).
        cart = self.cart_service.cart
        cart_uuid = str(cart.uuid) if cart else ""
        ownership = Q(pk__in=[])  # default: nothing
        if request.user.is_authenticated:
            ownership |= Q(reserved_by=request.user)
        if cart_uuid:
            ownership |= Q(session_id=cart_uuid)

        owned_ids = set(
            StockReservation.objects.filter(
                ownership, id__in=reservation_ids
            ).values_list("id", flat=True)
        )

        released_count = 0
        failed_releases = []

        for reservation_id in reservation_ids:
            try:
                owned = int(reservation_id) in owned_ids
            except TypeError, ValueError:
                owned = False

            if not owned:
                failed_releases.append(
                    {
                        "reservation_id": reservation_id,
                        "error": "Reservation not found for this cart",
                    }
                )
                continue

            try:
                StockManager.release_reservation(reservation_id)
                released_count += 1
            except StockReservationError:
                # Track failed releases but continue processing others.
                # Details go to the server log only — exception text must
                # not reach the response body (CodeQL
                # py/stack-trace-exposure).
                logger.warning(
                    "Failed to release stock reservation %s",
                    reservation_id,
                    exc_info=True,
                )
                failed_releases.append(
                    {
                        "reservation_id": reservation_id,
                        "error": _(
                            "Release failed — the reservation may already "
                            "be released or expired."
                        ),
                    }
                )

        # Return success even if some releases failed
        # (they may have already been released or expired)
        response_data: dict[str, str | int | list] = {
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
        from shipping.utils import compute_total_weight_grams

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

        # Wholesale minimum-order-value gate — refuse to mint an intent
        # order-create would reject AFTER the customer confirmed (and
        # possibly captured) the payment.
        from b2b.services import B2BPricingService, B2BService  # noqa: PLC0415

        unmet_minimum = B2BPricingService.min_order_value_unmet(cart)
        if unmet_minimum is not None:
            return Response(
                {
                    "detail": str(
                        _(
                            "The order total is below this wholesale "
                            "tier's minimum of {minimum}."
                        ).format(minimum=unmet_minimum)
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Validate the request body so the PaymentIntent amount is
        # always computed against the carrier the shopper picked —
        # not the generic ``FREE_SHIPPING_THRESHOLD`` fallback. Any
        # missing/invalid field surfaces as a 400 rather than a silent
        # mismatch at order-create time.
        request_serializer = CartCreatePaymentIntentRequestSerializer(
            data=request.data
        )
        request_serializer.is_valid(raise_exception=True)
        pay_way_id = request_serializer.validated_data["pay_way_id"]
        # ``shipping_provider_code`` is normalised to None by the
        # serializer's validate() when omitted (home_delivery is
        # provider-agnostic in the frontend); pass it through so the
        # order-create verification gets the same None and both calc
        # paths agree on the generic-fallback shipping price.
        shipping_provider_code = request_serializer.validated_data.get(
            "shipping_provider_code"
        )
        shipping_kind = request_serializer.validated_data["shipping_kind"]
        country_id = request_serializer.validated_data.get("country_id") or None
        region_id = request_serializer.validated_data.get("region_id") or None

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

        # Calculate cart total including shipping and payment method fee
        # using the SAME inputs the order-create verification step uses
        # (per-carrier free-shipping threshold + weight-banded quote +
        # country/region multipliers + promotion discount). The intent
        # must equal what ``OrderService.create_order_*`` will charge —
        # otherwise ``PaymentAmountMismatchError`` raises at
        # order-create time.
        from promotion.services import PromotionEngine

        promo_result = PromotionEngine.evaluate(
            cart,
            user=request.user if request.user.is_authenticated else None,
            email=request_serializer.validated_data.get("email") or "",
        )

        cart_total = cart.total_price
        cart_weight_grams = compute_total_weight_grams(
            (item.product, item.quantity) for item in cart.items.all()
        ) + PromotionEngine.gift_weight_grams(promo_result)
        if promo_result.free_shipping:
            shipping_cost = Money(0, cart_total.currency)
        else:
            shipping_cost = OrderService.calculate_shipping_cost(
                order_value=cart_total,
                country_id=country_id,
                region_id=region_id,
                shipping_provider_code=shipping_provider_code,
                shipping_kind=shipping_kind,
                weight_grams=cart_weight_grams,
            )
        # Pay-way fee (and its free_threshold) is computed on the
        # DISCOUNTED subtotal — the same figure order creation uses.
        order_subtotal = Money(
            cart_total.amount
            - promo_result.discount_total.amount
            + shipping_cost.amount,
            cart_total.currency,
        )
        payment_fee = OrderService.calculate_payment_method_fee(
            pay_way, order_subtotal
        )
        cart_total = Money(
            order_subtotal.amount + payment_fee.amount,
            cart_total.currency,
        )

        # Loyalty discount reduces the charge — same pricing math as
        # redeem_points (validated, unquantised Decimal), so the intent
        # equals what order creation persists as paid_amount.
        loyalty_points = request_serializer.validated_data.get(
            "loyalty_points_to_redeem"
        )
        # Wholesale carts sit outside the loyalty program unless the
        # merchant opts in — drop it here too, so this intent matches
        # what order creation will charge (which drops it as well).
        if B2BService.suppresses_loyalty(cart):
            loyalty_points = 0
        if loyalty_points and loyalty_points > 0:
            if not request.user.is_authenticated:
                return Response(
                    {
                        "detail": _(
                            "Loyalty redemption requires a signed-in customer."
                        ),
                        "reason": "loyalty_requires_authentication",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            from django.core.exceptions import (
                ValidationError as DjangoValidationError,
            )

            from loyalty.services import LoyaltyService

            try:
                loyalty_preview = LoyaltyService.preview_redemption(
                    request.user,
                    loyalty_points,
                    str(cart_total.currency),
                    max_discount=cart.total_price.amount,
                )
            except DjangoValidationError as exc:
                message = getattr(exc, "message", None) or "; ".join(
                    str(msg) for msg in getattr(exc, "messages", [])
                )
                return Response(
                    {
                        "detail": message,
                        "reason": "loyalty_redemption_invalid",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            cart_total = Money(
                max(cart_total.amount - loyalty_preview, 0),
                cart_total.currency,
            )

        # Gift cards settle part of the total LAST (payment, not
        # discount) — the intent covers the REMAINDER only. Same plan
        # math as the order-create verification (which recomputes it
        # under card locks), so the two always agree.
        gift_card_codes = request_serializer.validated_data.get(
            "gift_card_codes"
        )
        if gift_card_codes:
            from giftcard.services import GiftCardError, GiftCardService

            try:
                gift_plan = GiftCardService.plan_redemption(
                    gift_card_codes, cart_total
                )
            except GiftCardError as exc:
                return Response(
                    {"detail": exc.message, "reason": exc.reason},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            cart_total = Money(
                cart_total.amount - gift_plan.amount.amount,
                cart_total.currency,
            )

        if cart_total.amount <= 0:
            # Nothing left to charge — gift cards, a 100% promotion or
            # a full loyalty redemption cover everything. The frontend
            # must skip the PaymentIntent and submit the order with
            # the deductions alone; the order-first path settles it.
            return Response(
                {
                    "detail": _(
                        "The applied discounts and gift cards cover "
                        "the full total; no payment intent is needed."
                    ),
                    "reason": "gift_card_covers_total"
                    if gift_card_codes
                    else "nothing_to_charge",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

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

            # Serialize through CartPaymentIntentResponseSerializer (the
            # declared response serializer) so the payload matches the
            # OpenAPI contract the Nuxt client validates against. In
            # particular ``amount`` MUST be a JSON number (DecimalField with
            # COERCE_DECIMAL_TO_STRING=False) — returning a raw dict with
            # ``str(amount)`` failed the client's Zod ``z.number()`` gate and
            # broke every cart-Stripe checkout with a 422.
            response_data = {
                "client_secret": str(payment_data.get("client_secret", "")),
                "payment_intent_id": str(payment_data.get("payment_id", "")),
                "amount": cart_total.amount,
                "currency": currency_code,
            }
            response_serializer_class = self.get_response_serializer()
            response_serializer = response_serializer_class(response_data)
            return Response(
                response_serializer.data,
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
