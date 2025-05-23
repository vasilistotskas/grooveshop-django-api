from django.utils.translation import gettext_lazy as _
from djmoney.contrib.django_rest_framework import MoneyField
from rest_framework import serializers

from order.models.item import OrderItem
from product.serializers.product import ProductSerializer


class OrderItemSerializer(serializers.ModelSerializer):
    product = ProductSerializer()
    price = MoneyField(max_digits=11, decimal_places=2)
    total_price = MoneyField(max_digits=11, decimal_places=2, read_only=True)
    refunded_amount = MoneyField(
        max_digits=11, decimal_places=2, read_only=True
    )
    net_price = MoneyField(max_digits=11, decimal_places=2, read_only=True)

    class Meta:
        model = OrderItem
        fields = (
            "id",
            "price",
            "product",
            "quantity",
            "original_quantity",
            "is_refunded",
            "refunded_quantity",
            "net_quantity",
            "sort_order",
            "created_at",
            "updated_at",
            "uuid",
            "total_price",
            "refunded_amount",
            "net_price",
            "notes",
        )
        read_only_fields = (
            "created_at",
            "updated_at",
            "uuid",
            "total_price",
            "original_quantity",
            "is_refunded",
            "refunded_quantity",
            "net_quantity",
            "refunded_amount",
            "net_price",
        )


class OrderItemCreateUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItem
        fields = (
            "id",
            "product",
            "quantity",
            "notes",
        )

    def validate_quantity(self, value: int) -> int:
        if value <= 0:
            raise serializers.ValidationError(
                _("Quantity must be a positive number.")
            )
        return value

    def validate(self, data: dict) -> dict:
        product = data.get("product")
        quantity = data.get("quantity")

        if product and hasattr(product, "stock") and product.stock < quantity:
            raise serializers.ValidationError(
                {
                    "quantity": _(
                        "Not enough stock available. Only {product_stock} remaining."
                    ).format(
                        product_stock=product.stock,
                    )
                }
            )

        return data


class OrderItemRefundSerializer(serializers.Serializer):
    quantity = serializers.IntegerField(
        min_value=1,
        required=False,
        help_text=_("Quantity to refund. If not provided, refunds all."),
    )
    reason = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=255,
        help_text=_("Optional reason for the refund"),
    )

    def validate(self, data):
        item = self.context.get("item")
        quantity = data.get("quantity")

        if not item:
            raise serializers.ValidationError(
                _("Order item is required for refund operation")
            )

        if quantity and quantity > item.quantity - item.refunded_quantity:
            raise serializers.ValidationError(
                _(
                    "Cannot refund more than the available quantity. Available: {available_quantity}"
                ).format(
                    available_quantity=item.quantity - item.refunded_quantity
                )
            )

        return data
