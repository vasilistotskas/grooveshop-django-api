from djmoney.contrib.django_rest_framework import MoneyField
from rest_framework import serializers

from order.models.item import OrderItem
from product.serializers.product import ProductSerializer


class OrderItemSerializer(serializers.ModelSerializer):
    product = ProductSerializer()
    price = MoneyField(max_digits=19, decimal_places=4)
    total_price = MoneyField(max_digits=19, decimal_places=4, read_only=True)

    class Meta:
        model = OrderItem
        fields = (
            "id",
            "price",
            "product",
            "quantity",
            "created_at",
            "updated_at",
            "uuid",
            "sort_order",
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
            "id",
            "product",
            "quantity",
        )
