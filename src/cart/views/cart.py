from __future__ import annotations

import logging

from django.conf import settings
from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from drf_spectacular.openapi import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema_view,
)
from knox.models import get_token_model
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from cart.agent_token import CART_TOKEN_PARAMETER, mint_cart_token
from cart.filters.cart import CartFilter
from cart.models import Cart
from cart.serializers.cart import (
    CartAgentTokenResponseSerializer,
    CartCreatePaymentIntentRequestSerializer,
    CartPaymentIntentResponseSerializer,
    CartQuoteRequestSerializer,
    CartSerializer,
    CartWriteSerializer,
    CouponApplyRequestSerializer,
    CouponErrorResponseSerializer,
    ReleaseReservationsRequestSerializer,
    ReleaseReservationsResponseSerializer,
    ReserveStockResponseSerializer,
)
from cart.services import CartService
from cart.throttles import (
    CartAgentTokenThrottle,
    CartMutationAnonThrottle,
    CartMutationThrottle,
    CartQuoteAnonThrottle,
    CartQuoteThrottle,
    CouponApplyThrottle,
)
from core.api.action_config import (
    ActionConfig,
    SerializersConfig,
    create_schema_view_config,
)
from core.api.permissions import StoreStaffModelPermissions
from core.api.serializers import ErrorResponseSerializer
from core.api.throttling import AnonIpRateThrottle, UserBudgetThrottle
from core.api.views import BaseModelViewSet
from core.utils.i18n import active_language_code
from giftcard.throttles import gift_card_code_throttles
from order.checkout.quote import (
    CheckoutQuote,
    OrderQuoteService,
    ShippingChoice,
)
from order.enum.create_error import OrderCreateErrorType
from order.exceptions import (
    CartStockUnavailableError,
    InvalidGiftCardError,
    InvalidOrderDataError,
    LoyaltyRedemptionError,
    PickupPointUnfitError,
    ShippingRefusedError,
    StockReservationError,
)
from order.inventory.stock import StockManager
from order.models import StockReservation
from order.payments.payment import provider_supports
from order.payments.payment_binding import PaymentPurpose
from order.serializers.quote import CartQuoteSerializer
from order.services import (
    LEGAL_DOCUMENT_UNAVAILABLE_DETAIL,
    OrderLegalAcceptanceService,
)
from page_config.models import LegalDocumentUnavailableError
from pay_way.enum.settlement import PaySettlement
from pay_way.models import PayWay
from pay_way.services import PayWayService
from promotion.serializers import CartCouponSerializer
from tenant.membership import is_store_staff
from tenant.permissions import (
    IsCartEnabled,
    IsCatalogueEnabled,
    IsPromotionsEnabled,
)

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
            "from ``UUIDModel``."
        ),
        required=False,
    ),
]

CART_TOKEN_HEADERS = [*GUEST_CART_HEADERS, CART_TOKEN_PARAMETER]

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
        response=CartSerializer,
        operation_id="retrieveCart",
        summary=_("Get cart"),
        description=_(
            "Get a cart. For guest users, include X-Cart-Id header to maintain cart session."
        ),
        tags=["Cart"],
        parameters=CART_TOKEN_HEADERS,
    ),
    "update": ActionConfig(
        request=CartWriteSerializer,
        response=CartSerializer,
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
        response=CartSerializer,
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
            "Creates temporary stock reservations that last the store's "
            "reservation time. "
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
    "quote": ActionConfig(
        request=CartQuoteRequestSerializer,
        response=CartQuoteSerializer,
        operation_id="quoteCart",
        summary=_("Quote the cart's checkout total"),
        description=_(
            "What checkout charges for the cart with the choices made so "
            "far, computed by the function order creation and the payment "
            "intent use: the products, the promotions, the delivery, the "
            "pay-way fee, the loyalty discount and the gift cards, and the "
            "amount due. A row that depends on a choice the request did "
            "not make is null. Also lists every delivery and pay way on "
            "offer at the price this cart would be charged. Send "
            "``expectedTotal`` back when creating the order: it is then "
            "created only at this total. A quote asked with "
            "``X-Cart-Token`` is the cart owner's price."
        ),
        tags=["Cart"],
        parameters=CART_TOKEN_HEADERS,
    ),
    "apply_coupon": ActionConfig(
        request=CouponApplyRequestSerializer,
        response=CartSerializer,
        responses={400: CouponErrorResponseSerializer},
        operation_id="applyCartCoupon",
        summary=_("Apply a coupon code to the cart"),
        description=_(
            "Attach a coupon code to the cart. Returns the updated cart "
            "with promotion discount fields. Rejections carry a "
            "machine-readable reason."
        ),
        tags=["Cart"],
        parameters=CART_TOKEN_HEADERS,
    ),
    "remove_coupon": ActionConfig(
        response=CartSerializer,
        operation_id="removeCartCoupon",
        summary=_("Remove the applied coupon code from the cart"),
        tags=["Cart"],
        parameters=CART_TOKEN_HEADERS,
    ),
    "agent_token": ActionConfig(
        response=CartAgentTokenResponseSerializer,
        operation_id="createCartAgentToken",
        summary=_("Mint a cart token for the shopping assistant"),
        description=_(
            "Mint a short-lived token that lets the shopping assistant act "
            "on the signed-in shopper's own cart: read it, add, update and "
            "remove items, apply and remove coupons. Nothing else: not "
            "payment, stock reservation, checkout or deleting the cart. "
            "Send it as the ``X-Cart-Token`` header. It stops working when "
            "it expires or the shopper signs out; mint a new one when a "
            "call is refused with 401."
        ),
        tags=["Cart"],
    ),
    "list_coupons": ActionConfig(
        response=CartCouponSerializer,
        many=True,
        # A deliberately unpaginated array: the picker is one modal of
        # at most ``CouponPickerService.MAX_COUPONS`` rows, and a page
        # boundary inside it would hide the coupon a shopper came for.
        paginated=False,
        operation_id="listCartCoupons",
        summary=_("List the coupons available for this cart"),
        description=_(
            "Every coupon the shopper may be offered — the codes the "
            "store advertises publicly, plus the personal coupons "
            "assigned to the signed-in customer — each pre-judged "
            "against the cart as it stands. ``eligible`` is exactly "
            "what applying the code would do, so a disabled row and a "
            "refusal at apply time can never disagree; ``reason`` "
            "carries the ACP discount vocabulary and "
            "``discountAmount`` what the code would take off right "
            "now. Empty when the store has promotions disabled."
        ),
        tags=["Cart"],
        parameters=CART_TOKEN_HEADERS,
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

    # What an ``X-Cart-Token`` may do on the cart, spelled out: a
    # read, a quote, coupons, and (on the item routes) the lines.
    # Payment intents, stock reservation and cart delete or edit are
    # not here, so the token is refused there.
    _CART_TOKEN_ACTIONS = frozenset(
        {
            "retrieve",
            "quote",
            "apply_coupon",
            "remove_coupon",
            "list_coupons",
        }
    )

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self.cart_service = CartService(
            request=request,
            accepts_cart_token=self.action in self._CART_TOKEN_ACTIONS,
        )

    def get_permissions(self):
        if self.action == "list":
            # The staff listing of every cart: the store's staff keep it
            # whatever the shopper-facing cart does.
            self.permission_classes = [StoreStaffModelPermissions]
            return super().get_permissions()
        if self.action == "agent_token":
            self.permission_classes = [IsAuthenticated]
        elif self.action in {
            "apply_coupon",
            "remove_coupon",
            "list_coupons",
        }:
            # Guest-capable like the rest of the cart surface, but 404s
            # when the tenant's promotions plan flag is off.
            self.permission_classes = [IsPromotionsEnabled]
        else:
            # All other cart actions (retrieve, update, destroy, reserve_stock,
            # release_reservations, create_payment_intent) support guest users
            # via X-Cart-Id header — anonymous requests must be permitted.
            self.permission_classes = [AllowAny]
        # Every shopper-facing cart action answers 404 while the store
        # does not take orders; the gates fire before the rest.
        self.permission_classes = [
            IsCatalogueEnabled,
            IsCartEnabled,
            *self.permission_classes,
        ]
        return super().get_permissions()

    def get_throttles(self):
        if self.action == "agent_token":
            return [
                CartAgentTokenThrottle(),
                AnonIpRateThrottle(),
                UserBudgetThrottle(),
            ]
        if self.action == "apply_coupon":
            # Coupon apply is a brute-forceable code oracle — cap it far
            # tighter than the generic cart-mutation burst limits.
            return [
                CouponApplyThrottle(),
                CartMutationThrottle(),
                CartMutationAnonThrottle(),
                AnonIpRateThrottle(),
                UserBudgetThrottle(),
            ]
        if self.action == "quote":
            # A read with a budget of its own (``cart_quote``), so a
            # checkout that re-quotes on every change, or a platform
            # polling one, cannot use up the cart's write budget.
            return [
                *self._gift_card_code_throttles(),
                CartQuoteThrottle(),
                CartQuoteAnonThrottle(),
                AnonIpRateThrottle(),
                UserBudgetThrottle(),
            ]
        if self.action in self._MUTATION_ACTIONS:
            # Per-minute burst limits layered ON TOP of the global daily caps
            # (DEFAULT_THROTTLE_CLASSES) — include the defaults explicitly so
            # the override supplements rather than replaces them.
            throttles = [
                CartMutationThrottle(),
                CartMutationAnonThrottle(),
                AnonIpRateThrottle(),
                UserBudgetThrottle(),
            ]
            if self.action == "create_payment_intent":
                # The intent's amount is the quote's: it prices gift
                # cards too.
                return [*self._gift_card_code_throttles(), *throttles]
            return throttles
        return super().get_throttles()

    def _gift_card_code_throttles(self):
        """The gift-card guessing budget of the two actions that price a
        checkout, read through the action's own request serializer."""
        return gift_card_code_throttles(
            self.request, self.get_request_serializer()
        )

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
        return Response(
            {"detail": _("Method not allowed."), "code": "method_not_allowed"},
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    def retrieve(self, request, *args, **kwargs):
        # The service resolved the cart in ``initial()`` (and merged a
        # guest cart into it); only a visitor with no cart yet needs one
        # created here.
        cart = self.cart_service.cart or self.cart_service.get_or_create_cart()
        if not cart:
            return self._cart_not_found()
        # Re-load through for_detail() so the items are prefetched and the
        # totals are annotated: the cart_service returns a bare row whose
        # total_* properties would otherwise re-run get_items() several
        # times and the nested items serializer would N+1 per line (G0081).
        cart = CartService.load_for_detail(cart)
        response_serializer_class = self.get_response_serializer()
        response_serializer = response_serializer_class(cart)
        return Response(response_serializer.data, status=status.HTTP_200_OK)

    def update(self, request, *args, **kwargs):
        cart = self.cart_service.get_or_create_cart()
        if not cart:
            return self._cart_not_found()
        request_serializer_class = self.get_request_serializer()
        request_serializer = request_serializer_class(
            cart, data=request.data, partial=kwargs.pop("partial", False)
        )
        request_serializer.is_valid(raise_exception=True)
        self.perform_update(request_serializer)

        # Re-load optimized so the response serialization reads prefetched
        # items + annotated totals rather than re-querying per property/line
        # (G0081).
        cart = CartService.load_for_detail(cart)
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
        return self._cart_not_found()

    @staticmethod
    def _cart_not_found() -> Response:
        return Response(
            {"detail": _("Cart not found"), "code": "cart_not_found"},
            status=status.HTTP_404_NOT_FOUND,
        )

    def _cart_detail_response(self, cart, status_code=status.HTTP_200_OK):
        """Serialize the cart the same way ``retrieve`` does (optimized
        reload so items are prefetched and totals annotated)."""
        cart = CartService.load_for_detail(cart)
        response_serializer = CartSerializer(
            cart, context=self.get_serializer_context()
        )
        return Response(response_serializer.data, status=status_code)

    @action(detail=False, methods=["post"], url_path="coupon")
    def apply_coupon(self, request, *args, **kwargs):
        """Attach a coupon code to the cart and return the updated cart."""
        from promotion.coupons import CouponError, CouponService

        cart = self.cart_service.get_or_create_cart()
        if not cart:
            return Response(
                {"detail": _("Cart not found"), "code": "cart_not_found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        request_serializer_class = self.get_request_serializer()
        request_serializer = request_serializer_class(data=request.data)
        request_serializer.is_valid(raise_exception=True)

        try:
            CouponService.apply(
                cart,
                request_serializer.validated_data["code"],
                user=self.cart_service.owner,
            )
        except CouponError as exc:
            return Response(
                {
                    "detail": exc.message,
                    "reason": exc.reason,
                    "code": exc.reason,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return self._cart_detail_response(cart)

    @action(detail=False, methods=["delete"], url_path="coupon")
    def remove_coupon(self, request, *args, **kwargs):
        """Detach the applied coupon code from the cart."""
        from promotion.coupons import CouponService

        cart = self.cart_service.get_or_create_cart()
        if not cart:
            return Response(
                {"detail": _("Cart not found"), "code": "cart_not_found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        CouponService.remove(cart)
        return self._cart_detail_response(cart)

    @action(
        detail=False,
        methods=["get"],
        url_path="coupons",
        # A bare array, not a page: ``pagination_class=None`` is the
        # project's convention for saying so to the schema generator
        # (src/core/api/schema.py), and ``filter_backends=[]`` stops the
        # cart's admin FilterSet documenting query params this action
        # never reads.
        pagination_class=None,
        filter_backends=[],
    )
    def list_coupons(self, request, *args, **kwargs):
        """List the coupons this cart may use, each with its verdict.

        A read, so it neither creates a cart nor 404s without one: a
        shopper with no cart yet simply has no coupons to judge.
        """
        from promotion.coupon_picker import CouponPickerService

        cart = self.cart_service.get_existing_cart()
        owner = self.cart_service.owner
        options = (
            CouponPickerService.available(
                cart,
                user=owner,
                email=getattr(owner, "email", "") or "",
            )
            if cart
            else []
        )
        serializer = CartCouponSerializer(options, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], url_path="agent-token")
    def agent_token(self, request, *args, **kwargs):
        """Mint a cart token for the signed-in shopper's own cart.

        Knox logins only: a token is bound to the login that minted it
        and dies with it. The cart is created when the shopper has none,
        as every other cart endpoint does.
        """
        if not isinstance(request.auth, get_token_model()):
            return Response(
                {
                    "detail": _("A signed-in session is required."),
                    "code": "session_required",
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        cart = self.cart_service.get_or_create_cart()
        token = mint_cart_token(cart, request.auth)
        return Response(
            {
                "token": token,
                "expires_in": settings.CART_AGENT_TOKEN_TTL_SECONDS,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="reserve-stock")
    def reserve_stock(self, request, *args, **kwargs):
        """
        Reserve stock for all cart items during checkout.

        This endpoint is called when the customer begins the checkout process.
        It creates temporary stock reservations (the store's
        STOCK_RESERVATION_TTL_MINUTES) for all items
        in the cart to prevent other customers from purchasing the same items
        while this customer completes payment.

        The reservation IDs returned should be stored by the frontend and used
        during order creation to convert reservations to permanent stock decrements.
        """
        # Get the cart for the current user or guest
        cart = self.cart_service.get_or_create_cart()
        if not cart:
            return Response(
                {"detail": "Cart not found", "code": "cart_not_found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Get cart items with product information
        cart_items = cart.get_items()

        if not cart_items:
            return Response(
                {
                    "detail": "Cart is empty. Cannot reserve stock for empty cart.",
                    "code": "cart_empty",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # The cart's holds are replaced as a whole: its own earlier holds
        # never count against it, and a product it no longer wants is freed.
        quantities: dict[int, int] = {}
        for item in cart_items:
            quantities[item.product_id] = (
                quantities.get(item.product_id, 0) + item.quantity
            )
        try:
            reservations = StockManager.reserve_cart_stock(cart, quantities)
        except Cart.DoesNotExist:
            return Response(
                {"detail": "Cart not found", "code": "cart_not_found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        except CartStockUnavailableError as exc:
            # Log it, or the resulting 409 is unanswerable from server logs.
            names = {
                item.product_id: item.product.safe_translation_getter(
                    "name", any_language=True
                )
                for item in cart_items
            }
            failed_items = []
            for shortfall in exc.shortfalls:
                logger.info(
                    "Stock reservation rejected: cart=%s product=%s "
                    "available=%s requested=%s",
                    cart.uuid,
                    shortfall.product_id,
                    shortfall.available,
                    shortfall.requested,
                )
                failed_items.append(
                    {
                        "product_id": shortfall.product_id,
                        "product_name": names.get(shortfall.product_id),
                        "available": shortfall.available,
                        "requested": shortfall.requested,
                    }
                )
            return Response(
                {
                    "detail": "Insufficient stock for one or more items",
                    "failed_items": failed_items,
                    "code": "insufficient_stock",
                },
                status=status.HTTP_409_CONFLICT,
            )
        reservation_ids = [reservation.id for reservation in reservations]

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
        # Validate through the serializer this action already DECLARES
        # in `serializers_config` and never instantiated. The raw list
        # went straight into `id__in`, so `{"reservationIds": ["abc"]}`
        # from an anonymous caller raised `ValueError` deep inside
        # queryset evaluation — an unhandled 500 with a traceback, on an
        # endpoint any visitor can reach. It also bounded nothing, so one
        # request could put an arbitrarily long `IN (...)` list on the
        # database.
        request_serializer = ReleaseReservationsRequestSerializer(
            data=request.data
        )
        if not request_serializer.is_valid():
            # `{"detail": ...}` rather than DRF's field-keyed default,
            # because that is the shape every other error on this action
            # already returns and the shape the storefront reads. The
            # message is the serializer's own, not a fixed string: a
            # list of 101 valid integers is not "a list of integers"
            # problem, and telling the caller it is sends them looking
            # in the wrong place.
            errors = request_serializer.errors.get("reservation_ids") or []
            detail = (
                str(errors[0])
                if errors
                else "reservation_ids must be a list of integers"
            )
            return Response(
                {"detail": detail, "code": "invalid_reservation_ids"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        reservation_ids = request_serializer.validated_data["reservation_ids"]

        if not reservation_ids:
            return Response(
                {
                    "detail": "reservation_ids is required",
                    "code": "reservation_ids_required",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Ownership gate: a reservation
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

        # ``consumed`` comes along because it decides the outcome below,
        # not just ownership.
        owned_state = dict(
            StockReservation.objects.filter(
                ownership, id__in=reservation_ids
            ).values_list("id", "consumed")
        )

        released_count = 0
        already_inactive_count = 0
        failed_releases = []

        for reservation_id in reservation_ids:
            try:
                key = int(reservation_id)
            except TypeError, ValueError:
                key = None

            if key is None or key not in owned_state:
                failed_releases.append(
                    {
                        "reservation_id": reservation_id,
                        "error": "Reservation not found for this cart",
                    }
                )
                continue

            if owned_state[key]:
                # Already consumed, which is the state this endpoint
                # exists to reach — so it is a no-op, not a failure.
                #
                # Placing an order CONSUMES its reservations, and the
                # checkout then releases the ids it was holding. That
                # raced on every offline order: the backend answered 200
                # carrying ``failed_releases``, the storefront read that
                # as a failure and showed an error toast on top of the
                # success one, and the shopper never reached the success
                # page (order #274, 2026-09-10). Reporting "the stock is
                # not held" as an error was the bug; the release itself
                # had nothing left to do.
                already_inactive_count += 1
                released_count += 1
                logger.info(
                    "Stock reservation %s was already inactive "
                    "(consumed by an order or a previous release) — "
                    "nothing to release",
                    key,
                )
                continue

            try:
                StockManager.release_reservation(reservation_id)
                released_count += 1
            except StockReservationError as exc:
                # The reason goes in the MESSAGE, not just ``exc_info``:
                # the production formatter builds its JSON from a format
                # string, so a traceback is appended as separate,
                # unparseable lines after the object and is lost to log
                # shipping. "Failed to release stock reservation 282"
                # with no reason is what made #274 take a log dive to
                # explain.
                logger.warning(
                    "Failed to release stock reservation %s: %s",
                    reservation_id,
                    exc,
                    exc_info=True,
                )
                failed_releases.append(
                    {
                        "reservation_id": reservation_id,
                        # Never the exception text — CodeQL
                        # py/stack-trace-exposure.
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

    def _offered_pay_way(
        self, pay_way_id: int, shipping: ShippingChoice | None
    ) -> PayWay | Response:
        """The pay way a checkout call names, or the response refusing it.

        Accepted exactly when the quote would list it
        (``OrderQuoteService.offered_pay_ways``): active, through a
        provider the store can charge with, and not excluded for the
        chosen delivery, a home delivery that names no carrier included.
        Order creation refuses the rest by the same rule
        (``OrderViewSet._validate_pay_way_for_order``), and minting an
        intent for one means the shopper CONFIRMS the card in Stripe.js
        and only then gets a 400 from order creation — money taken, no
        order, nothing to refund against.
        """
        pay_way = PayWay.objects.filter(id=pay_way_id).first()
        if pay_way is None:
            logger.error("Payment method with ID %s not found", pay_way_id)
            return Response(
                {
                    "detail": f"Payment method with ID {pay_way_id} not found",
                    "code": "pay_way_not_found",
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        allowed = OrderQuoteService.offered_pay_ways(shipping)
        if not allowed.filter(id=pay_way.id).exists():
            logger.info(
                "Pay way refused: pay_way=%s (provider=%s, active=%s) not "
                "offered for shipping_provider_code=%s shipping_kind=%s",
                pay_way.id,
                pay_way.provider_code,
                pay_way.active,
                shipping.provider_code if shipping else None,
                shipping.kind if shipping else None,
            )
            return Response(
                {
                    "detail": _(
                        "The selected payment method is not available "
                        "for this order."
                    ),
                    "reason": "pay_way_unavailable",
                    "code": "pay_way_unavailable",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return pay_way

    def _quote(
        self,
        cart: Cart,
        data: dict,
        *,
        pay_way: PayWay | None,
        shipping: ShippingChoice | None,
        name_shipping_refusal: bool = False,
    ) -> CheckoutQuote | Response:
        """The checkout quote for a request body (``data``, validated),
        or the 400 that refuses it.

        The one place the cart endpoints price a checkout: the payment
        intent's amount and ``POST /cart/quote`` both come from here,
        and ``OrderQuoteService.quote`` is what order creation runs.

        Points are priced only for the signed-in customer who owns the
        cart. A cart token names the owner without being the owner: its
        holder may not price the owner's points, whose refusal says the
        balance. So a token request with points is refused like a
        guest's, before anything is priced.

        ``name_shipping_refusal`` answers a delivery that cannot be
        priced with its own code (``ShippingRefusal``) instead of
        ``invalid_order_data``: the quote's answer, which a client
        branches on to drop the selection.
        """
        loyalty_points = data.get("loyalty_points_to_redeem")
        user = self.cart_service.owner
        if loyalty_points and (
            user is None or self.cart_service.token_cart is not None
        ):
            return Response(
                {
                    "detail": _(
                        "Loyalty redemption requires a signed-in customer."
                    ),
                    "reason": "loyalty_requires_authentication",
                    "code": "loyalty_requires_authentication",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            return OrderQuoteService.quote(
                cart,
                user=user,
                email=data.get("email") or "",
                pay_way=pay_way,
                shipping=shipping,
                loyalty_points=loyalty_points,
                gift_card_codes=data.get("gift_card_codes"),
            )
        except LoyaltyRedemptionError as exc:
            return Response(
                {
                    "detail": exc.reason,
                    "reason": exc.code,
                    "code": exc.code,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        except ShippingRefusedError as exc:
            if not name_shipping_refusal:
                return self._invalid_order_data(exc)
            return Response(
                {
                    "detail": exc.message,
                    "reason": exc.reason,
                    "code": exc.reason,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        except PickupPointUnfitError as exc:
            if not name_shipping_refusal:
                return self._invalid_order_data(exc)
            # Before InvalidOrderDataError, which it subclasses: the client
            # drops the locker and picks another one.
            return Response(
                {
                    "detail": exc.message,
                    "reason": exc.code,
                    "code": exc.code,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        except InvalidOrderDataError as exc:
            return self._invalid_order_data(exc)
        except InvalidGiftCardError as exc:
            return Response(
                {
                    "detail": exc.message,
                    "reason": exc.reason,
                    "code": exc.reason,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

    @staticmethod
    def _invalid_order_data(exc: InvalidOrderDataError) -> Response:
        """Order creation's refusal shape: a fixed detail and the
        field-scoped messages, never the exception's own text."""
        logger.warning("Invalid order data: %s", exc)
        return Response(
            {
                "detail": _("Invalid order data"),
                "field_errors": exc.field_errors,
                "code": "invalid_order_data",
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    @action(detail=False, methods=["post"], url_path="quote")
    def quote(self, request, *args, **kwargs):
        """What checkout charges for the cart with the choices made so far.

        The cart is found as order creation finds it
        (``get_existing_cart``): a quote never creates one. Stock, the
        cart's validity, the wholesale minimum and the legal texts are
        order creation's to judge; a quote only prices.
        """
        cart = self.cart_service.get_existing_cart()
        if not cart:
            return self._cart_not_found()
        if not cart.items.exists():
            return Response(
                {
                    "detail": _(
                        "Cart is empty. Please add items before checkout."
                    ),
                    "code": "cart_empty",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        request_serializer = self.get_request_serializer()(data=request.data)
        request_serializer.is_valid(raise_exception=True)
        data = request_serializer.validated_data
        country_id = data.get("country_id") or None
        region_id = data.get("region_id") or None

        # A delivery is priced once it is chosen: a kind and a country.
        shipping = None
        if data.get("shipping_kind") and country_id:
            shipping = ShippingChoice.of(data)
        pay_way = None
        if data.get("pay_way_id"):
            pay_way = self._offered_pay_way(data["pay_way_id"], shipping)
            if isinstance(pay_way, Response):
                return pay_way

        quote = self._quote(
            cart,
            data,
            pay_way=pay_way,
            shipping=shipping,
            name_shipping_refusal=True,
        )
        if isinstance(quote, Response):
            return quote

        response_serializer = self.get_response_serializer()(
            quote,
            context={
                "shipping_options": (
                    OrderQuoteService.shipping_options(
                        quote, country_id=country_id, region_id=region_id
                    )
                    if country_id
                    else None
                ),
                "pay_ways": OrderQuoteService.pay_way_options(quote, shipping),
            },
        )
        return Response(response_serializer.data, status=status.HTTP_200_OK)

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
        cart = self.cart_service.get_or_create_cart()
        if not cart:
            logger.error("Cart not found")
            return Response(
                {"detail": "Cart not found", "code": "cart_not_found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        if not cart.items.exists():
            logger.error("Cart is empty")
            return Response(
                {
                    "detail": "Cart is empty. Cannot create payment intent for empty cart.",
                    "code": "cart_empty",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Wholesale minimum-order-value gate — refuse to mint an intent
        # order-create would reject AFTER the customer confirmed (and
        # possibly captured) the payment.
        from b2b.services import B2BPricingService

        unmet_minimum = B2BPricingService.min_order_value_unmet(cart)
        if unmet_minimum is not None:
            return Response(
                {
                    "detail": str(
                        _(
                            "The order total is below this wholesale "
                            "tier's minimum of {minimum}."
                        ).format(minimum=unmet_minimum)
                    ),
                    "code": "wholesale_minimum_not_met",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # The legal texts the order will record (terms of use, privacy
        # policy) must be readable in this language. Order creation
        # refuses without them, and here the card would be confirmed
        # before it got the chance: refuse before any money moves.
        try:
            OrderLegalAcceptanceService.documents_in_force(
                active_language_code()
            )
        except LegalDocumentUnavailableError as e:
            logger.error(
                "Payment intent refused: legal page %r has no published "
                "text in %r",
                e.slug,
                e.language_code,
            )
            return Response(
                {
                    "detail": LEGAL_DOCUMENT_UNAVAILABLE_DETAIL,
                    "code": OrderCreateErrorType.LEGAL_DOCUMENT_UNAVAILABLE,
                },
                status=status.HTTP_409_CONFLICT,
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
        # The delivery as order creation reads it from the same body
        # (``ShippingChoice.of``), so both price the same shipping.
        shipping = ShippingChoice.of(request_serializer.validated_data)
        pay_way = self._offered_pay_way(pay_way_id, shipping)
        if isinstance(pay_way, Response):
            return pay_way
        logger.info(
            "Payment intent: pay_way=%s provider=%s settlement=%s",
            pay_way.id,
            pay_way.provider_code,
            pay_way.settlement,
        )

        # Two separate questions, neither of which is a vendor name:
        # is money taken at checkout (the PAY WAY's settlement), and can
        # this PSP mint a client-confirmed intent (the PROVIDER's
        # capability). This was ``provider_code != "stripe"``, which is
        # a proxy for the second and silently wrong the day a third PSP
        # arrives or Stripe gains a flow it lacks today.
        settlement = PaySettlement(pay_way.settlement)
        can_mint_intent = provider_supports(
            pay_way.provider_code, "supports_payment_intent"
        )
        if settlement != PaySettlement.ONLINE or not can_mint_intent:
            logger.info(
                "Payment intent refused: pay_way=%s provider=%s "
                "settlement=%s supports_payment_intent=%s",
                pay_way.id,
                pay_way.provider_code,
                settlement.value,
                can_mint_intent,
            )
            return Response(
                {
                    "detail": _(
                        "The selected payment method cannot be paid "
                        "with a card on this page."
                    ),
                    "reason": "pay_way_not_intent_capable",
                    "code": "pay_way_not_intent_capable",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # The amount is the checkout quote's: the one total order creation
        # computes too, so the intent equals what
        # ``OrderService.create_order_from_cart`` verifies it against.
        quote = self._quote(
            cart,
            request_serializer.validated_data,
            pay_way=pay_way,
            shipping=shipping,
        )
        if isinstance(quote, Response):
            return quote
        cart_total = quote.charges().amount_due
        gift_card_codes = request_serializer.validated_data.get(
            "gift_card_codes"
        )

        if cart_total.amount <= 0:
            # Nothing left to charge — gift cards, a 100% promotion or
            # a full loyalty redemption cover everything. The frontend
            # must skip the PaymentIntent and submit the order with
            # the deductions alone; the order-first path settles it.
            reason = (
                "gift_card_covers_total"
                if gift_card_codes
                else "nothing_to_charge"
            )
            return Response(
                {
                    "detail": _(
                        "The applied discounts and gift cards cover "
                        "the full total; no payment intent is needed."
                    ),
                    "reason": reason,
                    "code": reason,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Get Stripe payment provider
        provider = PayWayService.get_provider_for_pay_way(pay_way)
        if not provider:
            logger.error("Stripe payment provider not available")
            return Response(
                {
                    "detail": "Stripe payment provider not available",
                    "code": "payment_provider_unavailable",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Create payment intent
        try:
            success, payment_data = provider.process_payment(
                amount=cart_total,
                order_id=f"cart_{cart.uuid}",  # Temporary ID until order is created
                purpose=PaymentPurpose.CART,
                subject=str(cart.uuid),
            )

            # NEVER log `payment_data` whole: it carries
            # `client_secret`, which is a bearer credential — with the
            # (public) publishable key it permits retrieve, confirm and
            # cancel on that PaymentIntent. Anyone able to read the log
            # stream could cancel a customer's in-flight payment or
            # confirm it with their own payment method.
            # `src/order/payments/payment.py` is already careful to log only the
            # id and status.
            logger.info(
                "Payment intent creation result: success=%s, intent=%s, "
                "status=%s",
                success,
                payment_data.get("payment_id", "<none>"),
                payment_data.get("status", "<none>"),
            )

            if not success:
                # The provider's own message carries Stripe request ids
                # and internal codes; it stays in the log, and the client
                # gets a stable reason instead (same rule as the
                # release-failure path above).
                logger.error(
                    "Failed to create payment intent for cart %s: %s",
                    cart.uuid,
                    payment_data.get("error", "Unknown error"),
                )
                return Response(
                    {
                        "detail": _("Failed to create payment intent."),
                        "reason": "payment_intent_failed",
                        "code": "payment_intent_failed",
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

        except Exception:
            logger.exception("Error creating payment intent from cart")
            return Response(
                {
                    "detail": "An error occurred while creating payment intent",
                    "code": "payment_intent_failed",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
