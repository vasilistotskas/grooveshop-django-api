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
        """POST /api/v1/giftcard/purchase — create purchase + Stripe PI.

        Online card payment only: a gift card is delivered by email the
        moment payment confirms, which rules COD out, and Viva's
        redirect flow is built around Order rows. The webhook branch in
        ``handle_stripe_payment_succeeded`` mints the card.
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

        if not PayWayService.is_provider_configured("stripe"):
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
            provider_code="stripe",
        )

        provider = get_payment_provider("stripe")
        success, payment_data = provider.process_payment(
            amount=amount,
            order_id=f"giftcard_{purchase.uuid}",
            order_uuid=f"giftcard_{purchase.uuid}",
            customer_email=buyer_email,
        )
        if not success:
            from giftcard.enum import GiftCardPurchaseStatus

            purchase.status = GiftCardPurchaseStatus.FAILED
            purchase.save(update_fields=["status"])
            logger.error(
                "Gift card purchase PI creation failed: %s",
                payment_data.get("error"),
            )
            return Response(
                {
                    "detail": _("Failed to start the payment."),
                    "reason": "gift_card_payment_failed",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        purchase.payment_id = payment_data["payment_id"]
        purchase.save(update_fields=["payment_id"])

        response_serializer_class = self.get_response_serializer()
        response_serializer = response_serializer_class(
            {
                "purchase_uuid": purchase.uuid,
                "client_secret": payment_data["client_secret"],
                "payment_intent_id": payment_data["payment_id"],
                "amount": amount.amount,
                "currency": str(amount.currency),
            }
        )
        return Response(
            response_serializer.data, status=status.HTTP_201_CREATED
        )

    @action(detail=False, methods=["GET"])
    def mine(self, request):
        """GET /api/v1/giftcard/mine — cards linked to my account."""
        cards = (
            GiftCard.objects.filter(issued_to=request.user)
            .prefetch_related("transactions")
            .order_by("-created_at")
        )
        response_serializer_class = self.get_response_serializer()
        serializer = response_serializer_class(
            cards, many=True, context=self.get_serializer_context()
        )
        return Response(serializer.data, status=status.HTTP_200_OK)
