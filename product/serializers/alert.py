from __future__ import annotations

from django.utils.translation import gettext_lazy as _
from djmoney.contrib.django_rest_framework import MoneyField
from rest_framework import serializers

from product.models.alert import ProductAlert, ProductAlertKind


class ProductAlertSerializer(serializers.ModelSerializer[ProductAlert]):
    target_price = MoneyField(
        max_digits=11, decimal_places=2, required=False, allow_null=True
    )

    class Meta:
        model = ProductAlert
        fields = (
            "id",
            "uuid",
            "kind",
            "product",
            "user",
            "email",
            "target_price",
            "is_active",
            "notified_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "uuid",
            "user",
            "is_active",
            "notified_at",
            "created_at",
            "updated_at",
        )

    def validate(self, attrs):
        kind = attrs.get("kind")
        target_price = attrs.get("target_price")
        if kind == ProductAlertKind.PRICE_DROP and not target_price:
            raise serializers.ValidationError(
                {
                    "target_price": _(
                        "target_price is required for price-drop alerts."
                    )
                }
            )
        return attrs
