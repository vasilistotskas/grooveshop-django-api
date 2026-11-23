"""Serializers for ``GET /api/v1/shipping/product/<product_id>``.

The product page's "shipping from X" line: what the cheapest delivery
of that one product costs, priced as checkout would price it
(``ShippingService.product_shipping_from``).
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers


class ProductShippingEstimateSerializer(serializers.Serializer):
    """The cheapest delivery of one product bought on its own."""

    country_code = serializers.CharField(
        max_length=2,
        help_text=_(
            "The country it was priced for: the store's first shipping "
            "country, the one checkout preselects."
        ),
    )
    price = serializers.DecimalField(
        source="price.amount",
        max_digits=11,
        decimal_places=2,
        help_text=_(
            "The cheapest delivery checkout would charge a cart holding "
            "this product alone, after its free-shipping threshold and "
            "free-shipping promotions."
        ),
    )
    currency = serializers.CharField(
        source="price.currency",
        max_length=3,
        help_text=_("Currency of ``price`` and ``free_shipping_threshold``."),
    )
    free = serializers.BooleanField(
        help_text=_("The product ships free on its own: ``price`` is 0."),
    )
    free_shipping_threshold = serializers.DecimalField(
        max_digits=11,
        decimal_places=2,
        allow_null=True,
        help_text=_(
            "The lowest products subtotal, after discounts, from which a "
            "carrier that can take this product ships free. Null when "
            "none ever does."
        ),
    )


class ProductShippingSerializer(serializers.Serializer):
    """Response payload of the product shipping endpoint."""

    estimate = ProductShippingEstimateSerializer(
        allow_null=True,
        help_text=_(
            "Null when the store ships nowhere, or no delivery it offers "
            "can take this product."
        ),
    )
