from __future__ import annotations

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers


class AgentProfileSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text=_("User ID"))
    email = serializers.EmailField(help_text=_("Account email"))
    first_name = serializers.CharField(
        allow_blank=True, help_text=_("First name")
    )
    last_name = serializers.CharField(
        allow_blank=True, help_text=_("Last name")
    )


class AgentFavouriteSerializer(serializers.Serializer):
    """Compact favourite row for agents — the storefront's favourite
    serializers embed the full product detail payload, which is far more
    than a tool result should carry."""

    product_id = serializers.IntegerField(help_text=_("Product ID"))
    name = serializers.CharField(help_text=_("Localized product name"))
    final_price = serializers.CharField(
        help_text=_("Current VAT-inclusive price")
    )
    currency = serializers.CharField(help_text=_("Price currency"))
    in_stock = serializers.BooleanField(
        help_text=_("Whether the product is currently in stock")
    )
    added_at = serializers.DateTimeField(
        help_text=_("When the product was favourited")
    )
