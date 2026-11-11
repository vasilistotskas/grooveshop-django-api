from djmoney.contrib.django_rest_framework import MoneyField
from rest_framework import serializers

from product.models.price_spell import ProductPriceSpell


class ProductPriceSpellSerializer(
    serializers.ModelSerializer[ProductPriceSpell]
):
    """One period the consumer gross unit price was constant; ``valid_to``
    is ``null`` for the price in force now."""

    price = MoneyField(max_digits=11, decimal_places=2, read_only=True)

    class Meta:
        model = ProductPriceSpell
        fields = ("valid_from", "valid_to", "price")
        read_only_fields = fields


class ProductPriceHistorySerializer(serializers.Serializer):
    """Payload for ``GET /product/{id}/price-history``: every spell,
    oldest first, so a client filters its own ranges."""

    currency = serializers.CharField(read_only=True)
    spells = ProductPriceSpellSerializer(many=True, read_only=True)
