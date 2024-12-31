from typing import override

from djmoney.contrib.django_rest_framework import MoneyField
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from cart.models import Cart, CartItem
from core.api.serializers import BaseExpandSerializer
from product.serializers.product import ProductSerializer


class CartItemSerializer(BaseExpandSerializer):
    cart = serializers.SerializerMethodField()
    product = serializers.SerializerMethodField()
    price = MoneyField(max_digits=11, decimal_places=2, read_only=True)
    final_price = MoneyField(max_digits=11, decimal_places=2, read_only=True)
    discount_value = MoneyField(max_digits=11, decimal_places=2, read_only=True)
    vat_value = MoneyField(max_digits=11, decimal_places=2, read_only=True)
    total_price = MoneyField(max_digits=11, decimal_places=2, read_only=True)
    total_discount_value = MoneyField(
        max_digits=11, decimal_places=2, read_only=True
    )

    @extend_schema_field(ProductSerializer)
    def get_product(self, obj):
        return ProductSerializer(obj.product).data

    @extend_schema_field(serializers.IntegerField)
    def get_cart(self, obj):
        return obj.cart.id

    class Meta:
        model = CartItem
        fields = (
            "id",
            "cart",
            "product",
            "quantity",
            "price",
            "final_price",
            "discount_value",
            "price_save_percent",
            "discount_percent",
            "vat_percent",
            "vat_value",
            "total_price",
            "total_discount_value",
            "created_at",
            "updated_at",
            "uuid",
        )
        read_only_fields = (
            "price",
            "final_price",
            "discount_value",
            "price_save_percent",
            "discount_percent",
            "vat_percent",
            "vat_value",
            "total_price",
            "total_discount_value",
            "created_at",
            "updated_at",
            "uuid",
        )


class CartItemCreateSerializer(BaseExpandSerializer):
    class Meta:
        model = CartItem
        fields = ("id", "product", "quantity")
        read_only_fields = ("id",)

    @override
    def create(self, validated_data):
        cart = self.context.get("cart")
        product = validated_data.get("product")
        quantity = validated_data.get("quantity")

        if not cart:
            raise serializers.ValidationError("Cart is not provided.")

        cart_item, created = CartItem.objects.get_or_create(
            cart=cart, product=product, defaults={"quantity": quantity}
        )
        if not created:
            cart_item.quantity += quantity
            cart_item.save()

        return cart_item


class CartSerializer(BaseExpandSerializer):
    cart_items = serializers.SerializerMethodField()
    total_price = MoneyField(max_digits=11, decimal_places=2, read_only=True)
    total_discount_value = MoneyField(
        max_digits=11, decimal_places=2, read_only=True
    )
    total_vat_value = MoneyField(
        max_digits=11, decimal_places=2, read_only=True
    )

    @extend_schema_field(serializers.ListSerializer(child=CartItemSerializer()))
    def get_cart_items(self, obj):
        cart_items = CartItem.objects.filter(cart=obj)
        return CartItemSerializer(
            cart_items, many=True, context=self.context
        ).data

    class Meta:
        model = Cart
        fields = (
            "id",
            "user",
            "total_price",
            "total_discount_value",
            "total_vat_value",
            "total_items",
            "total_items_unique",
            "cart_items",
            "created_at",
            "updated_at",
            "uuid",
            "last_activity",
        )
        read_only_fields = (
            "total_price",
            "total_discount_value",
            "total_vat_value",
            "total_items",
            "total_items_unique",
            "created_at",
            "updated_at",
            "uuid",
        )
