from __future__ import annotations

from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_serializer
from rest_framework import serializers

from product.serializers.product import EffectivePriceFieldsSerializer


class AgentProfileSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text=_("User ID"))
    email = serializers.EmailField(help_text=_("Account email"))
    first_name = serializers.CharField(
        allow_blank=True, help_text=_("First name")
    )
    last_name = serializers.CharField(
        allow_blank=True, help_text=_("Last name")
    )


# ``final_price`` stops at VAT and the markdown, so it can differ from
# what the shopper pays; ``effective_price`` is that amount. The agent
# gateway is the only client and reads ``effectivePrice`` next
# (docs/migrations.md: expand, then contract).
@extend_schema_serializer(deprecate_fields=["final_price"])
class AgentFavouriteSerializer(
    EffectivePriceFieldsSerializer, serializers.Serializer
):
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
