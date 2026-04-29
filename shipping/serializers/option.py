from decimal import Decimal

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from shipping.enum import ShippingKind


class ShippingOptionsQuerySerializer(serializers.Serializer):
    """Query params for ``GET /api/v1/shipping/options``."""

    country_code = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=2,
        help_text=_("ISO 3166-1 alpha-2 country code (e.g. 'GR')."),
    )
    order_value_amount = serializers.DecimalField(
        required=False,
        max_digits=11,
        decimal_places=2,
        default=Decimal("0"),
        help_text=_("Cart subtotal — used for free-shipping threshold checks."),
    )
    currency = serializers.CharField(
        required=False,
        max_length=3,
        default="EUR",
    )


class ShippingOptionSerializer(serializers.Serializer):
    """One row in the checkout shipping-method radio.

    Returned by :class:`shipping.views.ShippingOptionsView`.  The
    frontend renders one card per row; the ``kind`` value tells it
    whether to show a locker picker, and ``provider_code`` tells it
    which picker variant (BoxNow widget vs ACS server-side list).
    """

    provider_code = serializers.CharField()
    provider_name = serializers.CharField()
    kind = serializers.ChoiceField(choices=ShippingKind.choices)
    price = serializers.DecimalField(
        max_digits=11,
        decimal_places=2,
        allow_null=True,
        help_text=_("Null when the provider defers to the global flat rate."),
    )
    currency = serializers.CharField(max_length=3)
    live_mode = serializers.BooleanField()
    priority = serializers.IntegerField()
    metadata = serializers.DictField()
