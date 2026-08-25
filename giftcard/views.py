from __future__ import annotations

import logging

from django.utils.translation import gettext_lazy as _
from djmoney.money import Money
from drf_spectacular.utils import extend_schema_view
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle

from core.api.serializers import ErrorResponseSerializer
from core.api.throttling import (
    GiftCardCheckThrottle,
    PaymentAttemptAnonThrottle,
    PaymentAttemptThrottle,
)
from core.api.views import BaseModelViewSet
from core.utils.serializers import (
    ActionConfig,
    SerializersConfig,
    create_schema_view_config,
)
from giftcard.models import GiftCard, GiftCardPurchase
from giftcard.serializers import (
    GiftCardCheckRequestSerializer,
    GiftCardCheckResponseSerializer,
    GiftCardErrorResponseSerializer,
    GiftCardPurchaseRequestSerializer,
    GiftCardPurchaseResponseSerializer,
    GiftCardPurchaseStatusResponseSerializer,
    GiftCardSerializer,
)
from giftcard.services import GiftCardError, GiftCardService
from tenant.permissions import IsGiftCardsEnabled

logger = logging.getLogger(__name__)

serializers_config: SerializersConfig = {
    "check": ActionConfig(
        request=GiftCardCheckRequestSerializer,
        response=GiftCardCheckResponseSerializer,
        responses={400: GiftCardErrorResponseSerializer},
        operation_id="checkGiftCard",
        summary=_("Check a gift card's balance"),
        tags=["Gift Cards"],
    ),
    "purchase": ActionConfig(
        request=GiftCardPurchaseRequestSerializer,
        response=GiftCardPurchaseResponseSerializer,
        responses={400: GiftCardErrorResponseSerializer},
        operation_id="purchaseGiftCard",
        summary=_("Buy a gift card (Stripe payment)"),
        tags=["Gift Cards"],
    ),
    "mine": ActionConfig(
        response=GiftCardSerializer,
        many=True,
        operation_id="listMyGiftCards",
        summary=_("List the gift cards linked to my account"),
        tags=["Gift Cards"],
    ),
    "purchase_status": ActionConfig(
        response=GiftCardPurchaseStatusResponseSerializer,
        operation_id="getGiftCardPurchaseStatus",
        summary=_("Poll a gift card purchase's payment status"),
        tags=["Gift Cards"],
    ),
}


@extend_schema_view(
    **create_schema_view_config(
        model_class=GiftCard,
        display_config={"tag": "Gift Cards"},
        serializers_config=serializers_config,
        error_serializer=ErrorResponseSerializer,
    )
)
class GiftCardViewSet(BaseModelViewSet):
    """Shopper-facing gift-card endpoints.

    Balance check + purchase are guest-capable (gated by the tenant's
    gift-cards plan flag with 404 semantics); redemption itself happens
    at order creation via ``gift_card_codes``.
    """

    queryset = GiftCard.objects.none()
    serializers_config = serializers_config

    def get_permissions(self):
        if self.action == "mine":
            self.permission_classes = [IsGiftCardsEnabled, IsAuthenticated]
        else:
            self.permission_classes = [IsGiftCardsEnabled, AllowAny]
        return super().get_permissions()

    def get_throttles(self):
        if self.action == "purchase_status":
            # Same tight budget as the balance oracle — the poll leaks
            # nothing but must not be a free enumeration channel.
            return [
                GiftCardCheckThrottle(),
                AnonRateThrottle(),
                UserRateThrottle(),
            ]
        if self.action == "check":
            # Balance check is a bearer-code oracle — budget it tightly.
            return [
                GiftCardCheckThrottle(),
                AnonRateThrottle(),
                UserRateThrottle(),
            ]
        if self.action == "purchase":
            return [
                PaymentAttemptThrottle(),
                PaymentAttemptAnonThrottle(),
                AnonRateThrottle(),
                UserRateThrottle(),
            ]
        return super().get_throttles()

    @action(detail=False, methods=["POST"])
    def check(self, request):
        """POST /api/v1/giftcard/check — balance + redeemability."""
        request_serializer_class = self.get_request_serializer()
        serializer = request_serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            card = GiftCardService.check(serializer.validated_data["code"])
        except GiftCardError as exc:
            return Response(
                {"detail": exc.message, "reason": exc.reason},
                status=status.HTTP_400_BAD_REQUEST,
            )

        balance = card.balance
        response_serializer_class = self.get_response_serializer()
        response_serializer = response_serializer_class(
            {
                "code": card.code,
                "balance": balance.amount,
                "currency": str(balance.currency),
                "expires_at": card.expires_at,
                "is_redeemable": card.is_redeemable,
            }
        )
        return Response(response_serializer.data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["POST"])
    def purchase(self, request):
        """POST /api/v1/giftcard/purchase — start an online payment.

        Two provider flows, matching the store's checkout providers:
        stripe = inline PaymentIntent (clientSecret in the response,
        completed by ``handle_stripe_payment_succeeded``); viva_wallet
        = hosted Smart Checkout redirect (checkoutUrl in the response,
        completed by the Viva webhook's gift-card branch). Offline
        payment makes no sense for an email-delivered voucher.
        """
        from order.payment import get_payment_provider
        from pay_way.services import PayWayService

        if not GiftCardService.is_enabled():
            return Response(
                {
                    "detail": _("Gift cards are not available."),
                    "reason": "gift_card_invalid",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        request_serializer_class = self.get_request_serializer()
        serializer = request_serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        provider_code = data.get("payment_provider") or "stripe"

        user = request.user if request.user.is_authenticated else None
        buyer_email = data.get("buyer_email") or (user.email if user else "")
        if not buyer_email:
            return Response(
                {
                    "detail": _("Buyer email is required."),
                    "reason": "gift_card_buyer_email_required",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            GiftCardService.validate_purchase_amount(data["amount"])
        except GiftCardError as exc:
            return Response(
                {"detail": exc.message, "reason": exc.reason},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not PayWayService.is_provider_configured(provider_code):
            return Response(
                {
                    "detail": _(
                        "Gift card purchase is not available for this store."
                    ),
                    "reason": "gift_card_purchase_unavailable",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        amount = Money(data["amount"], GiftCardService.default_currency())
        purchase = GiftCardPurchase.objects.create(
            buyer=user,
            buyer_email=buyer_email,
            amount=amount,
            recipient_email=data["recipient_email"],
            recipient_name=data.get("recipient_name", ""),
            sender_name=data.get("sender_name", ""),
            message=data.get("message", ""),
            deliver_at=data.get("deliver_at"),
            provider_code=provider_code,
        )

        provider = get_payment_provider(provider_code)
        response_payload = {
            "purchase_uuid": purchase.uuid,
            "provider": provider_code,
            "amount": amount.amount,
            "currency": str(amount.currency),
        }

        if provider_code == "viva_wallet":
            # Hosted redirect: mint a Smart Checkout order. Its
            # orderCode is stored as payment_id — that is how BOTH the
            # webhook's gift-card branch and the viva_return resolver
            # find the purchase again.
            success, payment_data = provider.create_checkout_session(
                amount=amount,
                order_id=f"giftcard-{purchase.pk}",
                order_uuid=f"giftcard:{purchase.uuid}",
                description=str(_("Gift card {amount}").format(amount=amount)),
                customer_email=buyer_email,
            )
            if success:
                purchase.payment_id = str(payment_data["session_id"])
                purchase.save(update_fields=["payment_id"])
                response_payload["checkout_url"] = payment_data["checkout_url"]
        else:
            success, payment_data = provider.process_payment(
                amount=amount,
                order_id=f"giftcard_{purchase.uuid}",
                order_uuid=f"giftcard_{purchase.uuid}",
                customer_email=buyer_email,
            )
            if success:
                purchase.payment_id = payment_data["payment_id"]
                purchase.save(update_fields=["payment_id"])
                response_payload["client_secret"] = payment_data[
                    "client_secret"
                ]
                response_payload["payment_intent_id"] = payment_data[
                    "payment_id"
                ]

        if not success:
            from giftcard.enum import GiftCardPurchaseStatus

            purchase.status = GiftCardPurchaseStatus.FAILED
            purchase.save(update_fields=["status"])
            logger.error(
                "Gift card purchase payment start failed (%s): %s",
                provider_code,
                payment_data.get("error"),
            )
            return Response(
                {
                    "detail": _("Failed to start the payment."),
                    "reason": "gift_card_payment_failed",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        response_serializer_class = self.get_response_serializer()
        response_serializer = response_serializer_class(response_payload)
        return Response(
            response_serializer.data, status=status.HTTP_201_CREATED
        )

    @action(detail=False, methods=["GET"], url_path="purchase-status")
    def purchase_status(self, request):
        """GET /api/v1/giftcard/purchase-status?uuid= — poll a purchase.

        The Viva redirect races the webhook, so the storefront return
        page polls this until the purchase flips PAID/FAILED. The UUID
        is unguessable — same access model as guest orders.
        """
        purchase_uuid = (request.query_params.get("uuid") or "").strip()
        if not purchase_uuid:
            return Response(
                {"detail": _("Missing uuid.")},
                status=status.HTTP_400_BAD_REQUEST,
            )
        from django.core.exceptions import (
            ValidationError as DjangoValidationError,
        )

        try:
            purchase = GiftCardPurchase.objects.get(uuid=purchase_uuid)
        except (
            GiftCardPurchase.DoesNotExist,
            DjangoValidationError,
            ValueError,
        ):
            return Response(
                {"detail": _("Purchase not found.")},
                status=status.HTTP_404_NOT_FOUND,
            )

        response_serializer_class = self.get_response_serializer()
        response_serializer = response_serializer_class(
            {"purchase_uuid": purchase.uuid, "status": purchase.status}
        )
        return Response(response_serializer.data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["GET"])
    def mine(self, request):
        """GET /api/v1/giftcard/mine — cards linked to my account.

        Paginated envelope — the generated OpenAPI contract wraps
        ``many=True`` list actions in the standard paginator shape
        (loyalty transactions precedent).
        """
        cards = (
            GiftCard.objects.filter(issued_to=request.user)
            .prefetch_related("transactions")
            .order_by("-created_at")
        )
        response_serializer_class = self.get_response_serializer()
        return self.paginate_and_serialize(
            cards, request, serializer_class=response_serializer_class
        )
