"""Serializers for ``GET /api/v1/shipping/free-shipping-info``.

The frontend renders a single dynamic line — "Δωρεάν μεταφορικά άνω
των X €" — on the product detail page and the cart summary.  The
endpoint exposes per-carrier thresholds in addition to the aggregate
``min_threshold`` so future UI work (carrier-specific badges in the
checkout sidebar, "free shipping unlocked" copy on the order summary)
can build on the same payload without another round-trip.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from shipping.enum import ShippingKind


class FreeShippingProviderEntrySerializer(serializers.Serializer):
    """One (provider, kind) row in the free-shipping-info response."""

    provider_code = serializers.CharField()
    provider_name = serializers.CharField()
    kind = serializers.ChoiceField(choices=ShippingKind.choices)
    threshold = serializers.DecimalField(
        max_digits=11,
        decimal_places=2,
        help_text=_(
            "Cart subtotal in the response's currency above which "
            "this (provider, kind) ships free."
        ),
    )
    priority = serializers.IntegerField()


class FreeShippingInfoQuerySerializer(serializers.Serializer):
    """Query params for the free-shipping-info endpoint."""

    country_code = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=2,
        help_text=_(
            "Optional ISO 3166-1 alpha-2 filter applied against each "
            "provider's ``metadata['supported_countries']`` list."
        ),
    )
    currency = serializers.CharField(
        required=False,
        max_length=3,
        help_text=_(
            "Currency the response should advertise. Defaults to the "
            "platform default (settings.DEFAULT_CURRENCY)."
        ),
    )


class FreeShippingInfoSerializer(serializers.Serializer):
    """Response payload for the free-shipping-info endpoint."""

    providers = FreeShippingProviderEntrySerializer(many=True)
    min_threshold = serializers.DecimalField(
        max_digits=11,
        decimal_places=2,
        allow_null=True,
        help_text=_(
            "Lowest threshold across active providers — the headline "
            "'free shipping from X €' number. Null when no active "
            "provider advertises a threshold."
        ),
    )
    max_threshold = serializers.DecimalField(
        max_digits=11,
        decimal_places=2,
        allow_null=True,
        help_text=_(
            "Highest threshold across active providers — the subtotal "
            "at which every carrier ships free. Null when no active "
            "provider advertises a threshold."
        ),
    )
    currency = serializers.CharField(max_length=3)
