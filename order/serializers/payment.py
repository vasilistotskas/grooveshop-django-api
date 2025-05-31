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


class ProcessPaymentResponseSerializer(serializers.Serializer):
    detail = serializers.CharField()
    order_id = serializers.IntegerField()
    payment_status = serializers.CharField()
    payment_id = serializers.CharField()
    requires_confirmation = serializers.BooleanField()
    is_online_payment = serializers.BooleanField()
    provider_data = serializers.DictField()


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
