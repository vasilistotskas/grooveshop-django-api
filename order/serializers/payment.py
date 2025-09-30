from django.utils.translation import gettext_lazy as _
from rest_framework import serializers


class ProcessPaymentRequestSerializer(serializers.Serializer):
    pay_way_id = serializers.IntegerField(
        help_text=_("ID of the payment method to use")
    )
    payment_data = serializers.DictField(
        required=False,
        default=dict,
        help_text=_("Additional payment data required by the payment provider"),
    )

    payment_method_id = serializers.CharField(
        required=False, help_text=_("Stripe Payment Method ID (pm_...)")
    )
    customer_id = serializers.CharField(
        required=False, help_text=_("Stripe Customer ID (cus_...)")
    )
    return_url = serializers.URLField(
        required=False,
        help_text=_("URL to redirect to after payment confirmation"),
    )


class ProcessPaymentResponseSerializer(serializers.Serializer):
    detail = serializers.CharField()
    order_id = serializers.IntegerField()
    payment_status = serializers.CharField()
    payment_id = serializers.CharField()
    requires_confirmation = serializers.BooleanField()
    is_online_payment = serializers.BooleanField()
    provider_data = serializers.DictField()

    client_secret = serializers.CharField(
        required=False,
        help_text=_(
            "Stripe PaymentIntent client secret for frontend confirmation"
        ),
    )
    requires_action = serializers.BooleanField(
        default=False,
        help_text=_(
            "Whether the payment requires additional action (3D Secure, etc.)"
        ),
    )


class PaymentStatusResponseSerializer(serializers.Serializer):
    order_id = serializers.IntegerField()
    payment_status = serializers.CharField()
    is_paid = serializers.BooleanField()
    status_details = serializers.DictField()


class RefundRequestSerializer(serializers.Serializer):
    amount = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        required=False,
        help_text=_("Refund amount (optional, defaults to full refund)"),
    )
    currency = serializers.CharField(
        max_length=3,
        required=False,
        help_text=_("Currency code (required if amount is specified)"),
    )


class RefundResponseSerializer(serializers.Serializer):
    detail = serializers.CharField()
    order_id = serializers.IntegerField()
    payment_status = serializers.CharField()
    refund_id = serializers.CharField()
    refund_details = serializers.DictField()


class CreatePaymentIntentRequestSerializer(serializers.Serializer):
    """
    Serializer for creating a Stripe PaymentIntent.
    For manual confirmation flow, we only need optional payment_data.
    """

    payment_data = serializers.DictField(
        required=False,
        default=dict,
        help_text=_("Additional payment data required by the payment provider"),
    )


class CreatePaymentIntentResponseSerializer(serializers.Serializer):
    payment_id = serializers.CharField(
        help_text=_("Payment intent ID from the payment provider")
    )

    status = serializers.CharField(help_text=_("Payment status"))

    amount = serializers.CharField(help_text=_("Payment amount"))

    currency = serializers.CharField(help_text=_("Payment currency"))

    provider = serializers.CharField(help_text=_("Payment provider name"))

    client_secret = serializers.CharField(
        required=False,
        help_text=_(
            "Stripe PaymentIntent client secret for frontend confirmation"
        ),
    )

    requires_action = serializers.BooleanField(
        required=False,
        default=False,
        help_text=_(
            "Whether the payment requires additional action (3D Secure, etc.)"
        ),
    )

    next_action = serializers.DictField(
        required=False,
        allow_null=True,
        help_text=_("Next action required for payment completion"),
    )


class CreateCheckoutSessionRequestSerializer(serializers.Serializer):
    success_url = serializers.URLField(required=True)
    cancel_url = serializers.URLField(required=True)
    customer_email = serializers.EmailField(required=False)
    customer_id = serializers.CharField(required=False)
    description = serializers.CharField(required=False, max_length=500)


class CreateCheckoutSessionResponseSerializer(serializers.Serializer):
    session_id = serializers.CharField()
    checkout_url = serializers.URLField()
    status = serializers.CharField()
    amount = serializers.CharField()
    currency = serializers.CharField()
    provider = serializers.CharField()
