from django.utils.translation import gettext_lazy as _
from djmoney.contrib.django_rest_framework import MoneyField
from rest_framework import serializers

from giftcard.models import GiftCard, GiftCardTransaction


class GiftCardCheckRequestSerializer(serializers.Serializer):
    code = serializers.CharField(
        max_length=32,
        help_text=_("Gift card code (case-insensitive)"),
    )


class GiftCardCheckResponseSerializer(serializers.Serializer):
    code = serializers.CharField()
    balance = serializers.DecimalField(max_digits=11, decimal_places=2)
    currency = serializers.CharField(max_length=3)
    expires_at = serializers.DateTimeField(allow_null=True)
    is_redeemable = serializers.BooleanField()


class GiftCardErrorResponseSerializer(serializers.Serializer):
    detail = serializers.CharField(help_text=_("Human-readable message"))
    reason = serializers.CharField(
        help_text=_("Machine-readable reason, e.g. gift_card_invalid")
    )


class GiftCardPurchaseRequestSerializer(serializers.Serializer):
    amount = serializers.DecimalField(
        max_digits=11,
        decimal_places=2,
        min_value=0,
        help_text=_(
            "Card value in EUR — bounded by GIFT_CARD_MIN_AMOUNT / "
            "GIFT_CARD_MAX_AMOUNT"
        ),
    )
    buyer_email = serializers.EmailField(
        required=False,
        allow_blank=True,
        help_text=_(
            "Required for guests; authenticated buyers default to "
            "their account email"
        ),
    )
    recipient_email = serializers.EmailField()
    recipient_name = serializers.CharField(
        max_length=255, required=False, allow_blank=True, default=""
    )
    sender_name = serializers.CharField(
        max_length=255, required=False, allow_blank=True, default=""
    )
    message = serializers.CharField(
        max_length=2000, required=False, allow_blank=True, default=""
    )
    deliver_at = serializers.DateTimeField(
        required=False,
        allow_null=True,
        help_text=_("Empty means deliver right after payment"),
    )


class GiftCardPurchaseResponseSerializer(serializers.Serializer):
    purchase_uuid = serializers.UUIDField()
    client_secret = serializers.CharField(
        help_text=_("Stripe PaymentIntent client secret")
    )
    payment_intent_id = serializers.CharField()
    amount = serializers.DecimalField(max_digits=11, decimal_places=2)
    currency = serializers.CharField(max_length=3)


class GiftCardTransactionSerializer(
    serializers.ModelSerializer[GiftCardTransaction]
):
    class Meta:
        model = GiftCardTransaction
        fields = ("id", "kind", "amount", "order", "created_at")
        read_only_fields = fields


class GiftCardSerializer(serializers.ModelSerializer[GiftCard]):
    initial_value = MoneyField(max_digits=11, decimal_places=2, read_only=True)
    balance = serializers.SerializerMethodField()
    transactions = GiftCardTransactionSerializer(many=True, read_only=True)

    def get_balance(self, obj: GiftCard):
        return obj.balance.amount

    class Meta:
        model = GiftCard
        fields = (
            "id",
            "uuid",
            "code",
            "initial_value",
            "balance",
            "status",
            "expires_at",
            "recipient_email",
            "recipient_name",
            "sender_name",
            "message",
            "delivered_at",
            "transactions",
            "created_at",
        )
        read_only_fields = fields
