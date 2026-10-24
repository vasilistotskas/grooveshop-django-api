"""Serializers for ``GET /api/v1/shipping/policy``.

The storefront declares the store's shipping policy once, at
schema.org Organization level (``hasShippingService``), from this
payload. It carries only what the store maintains on its
``ShippingRate`` rows, so nothing in it is a promise the store did not
make.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from shipping.enum import ShippingKind


class ShippingPolicyEntrySerializer(serializers.Serializer):
    """One (provider, kind, country) the store ships."""

    country_code = serializers.CharField(
        max_length=2,
        help_text=_("ISO 3166-1 alpha-2 destination country."),
    )
    kind = serializers.ChoiceField(choices=ShippingKind.choices)
    provider_code = serializers.CharField()
    provider_name = serializers.CharField()
    price = serializers.DecimalField(
        max_digits=11,
        decimal_places=2,
        help_text=_(
            "The rate's own price for this (provider, country, kind), "
            "before a free-shipping threshold or a live carrier quote "
            "changes what a particular cart pays."
        ),
    )
    currency = serializers.CharField(
        max_length=3,
        help_text=_("Currency of ``price`` and ``free_shipping_threshold``."),
    )
    free_shipping_threshold = serializers.DecimalField(
        max_digits=11,
        decimal_places=2,
        allow_null=True,
        help_text=_(
            "Products subtotal, after discounts and excluding shipping, "
            "from which this combination ships free. Null when it is "
            "never free."
        ),
    )
    delivery_days_min = serializers.IntegerField(
        allow_null=True,
        help_text=_(
            "Fastest delivery in business days after dispatch, or null "
            "when the rate advertises no estimate."
        ),
    )
    delivery_days_max = serializers.IntegerField(
        allow_null=True,
        help_text=_(
            "Slowest delivery in business days after dispatch; null "
            "exactly when ``delivery_days_min`` is."
        ),
    )
    max_weight_grams = serializers.IntegerField(
        allow_null=True,
        help_text=_("Heaviest parcel the rate carries, or null for no cap."),
    )
