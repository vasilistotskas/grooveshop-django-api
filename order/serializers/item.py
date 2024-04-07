from djmoney.contrib.django_rest_framework import MoneyField

from core.api.serializers import BaseExpandSerializer
from order.models.item import OrderItem
from product.serializers.product import ProductSerializer


class OrderItemSerializer(BaseExpandSerializer):
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
            "created_at",
            "updated_at",
            "uuid",
            "sort_order",
            "total_price",
        )


class OrderItemCreateUpdateSerializer(BaseExpandSerializer):
    class Meta:
        model = OrderItem
        fields = (
            "id",
            "product",
            "price",
            "quantity",
        )


class CheckoutItemSerializer(BaseExpandSerializer):
    class Meta:
        model = OrderItem
        fields = (
            "product",
            "quantity",
        )
