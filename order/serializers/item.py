from djmoney.contrib.django_rest_framework import MoneyField
from rest_framework import serializers

from order.models.item import OrderItem
from product.serializers.product import ProductSerializer


class OrderItemSerializer(serializers.ModelSerializer):
    product = ProductSerializer()
    price = MoneyField(max_digits=11, decimal_places=2)
    total_price = MoneyField(max_digits=11, decimal_places=2, read_only=True)

    class Meta:
        model = OrderItem
        fields = (
            "id",
            "price",
            "product",
            "quantity",
            "sort_order",
            "created_at",
            "updated_at",
            "uuid",
            "total_price",
        )
        read_only_fields = (
            "created_at",
            "updated_at",
            "uuid",
            "total_price",
        )


class OrderItemCreateUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItem
        fields = (
            "id",
            "product",
            "price",
            "quantity",
        )


class CheckoutItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItem
        fields = (
            "product",
            "quantity",
        )
