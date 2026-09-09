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
        default=Decimal(0),
        help_text=_("Cart subtotal — used for free-shipping threshold checks."),
    )
    currency = serializers.CharField(
        required=False,
        max_length=3,
        default="EUR",
    )
    weight_grams = serializers.IntegerField(
        required=False,
        min_value=0,
        max_value=100_000,
        allow_null=True,
        help_text=_(
            "Total cart weight in grams. ACS uses it to bucket the live "
            "quote against the actual tariff bracket so the shopper sees "
            "the same price the voucher mint will charge. BoxNow ignores "
            "it. Defaults to the ACS minimum chargeable weight (500g) "
            "when omitted."
        ),
    )


class ShippingOptionPayWaySerializer(serializers.Serializer):
    """A payment method this shipping option can settle.

    ``name`` is the ``PayWayEnum`` KEY, not a display string — the same
    contract the pay-way endpoint uses, so the storefront resolves it
    through the label map it already owns rather than rendering
    whatever language the API happened to answer in.
    """

    id = serializers.IntegerField()
    name = serializers.CharField()


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
    logo_url = serializers.URLField(
        allow_null=True,
        required=False,
        help_text=_(
            "Absolute URL for the operator-uploaded brand logo, "
            "resolved per (provider, kind) so the home-delivery row "
            "and the pickup-point row of the same carrier can show "
            "different images. Null when no logo is uploaded — the "
            "storefront then falls back to its bundled default. "
            "``settings.MEDIA_URL`` is absolute in every environment "
            "so this is always a full URL when present."
        ),
    )
    metadata = serializers.DictField()
    pay_ways = ShippingOptionPayWaySerializer(
        many=True,
        help_text=_(
            "Active payment methods this (provider, kind) can settle, "
            "by the same rules checkout applies when the shopper picks "
            "it. Stated as a fact per option rather than as "
            "'exclusive to this row': the storefront collapses several "
            "carriers into one home-delivery card, so only it knows "
            "which rows it actually renders and can work out what a "
            "given choice uniquely unlocks. Lets the delivery step "
            "advertise a carrier-only product — BOX NOW Αντικαταβολή "
            "is reachable ONLY via a BoxNow locker, and a shopper who "
            "never picks one has no way to discover it exists."
        ),
    )
