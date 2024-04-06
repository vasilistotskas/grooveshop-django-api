from djmoney.contrib.django_rest_framework import MoneyField
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from cart.models import Cart
from cart.models import CartItem
from core.api.serializers import BaseExpandSerializer
from product.serializers.product import ProductSerializer


class CartItemSerializer(BaseExpandSerializer):
    cart = serializers.SerializerMethodField("get_cart_id")
    product = serializers.SerializerMethodField("get_product")
    price = MoneyField(max_digits=11, decimal_places=2, read_only=True)
    final_price = MoneyField(max_digits=11, decimal_places=2, read_only=True)
    discount_value = MoneyField(max_digits=11, decimal_places=2, read_only=True)
    vat_value = MoneyField(max_digits=11, decimal_places=2, read_only=True)
    total_price = MoneyField(max_digits=11, decimal_places=2, read_only=True)
    total_discount_value = MoneyField(max_digits=11, decimal_places=2, read_only=True)

    @extend_schema_field(ProductSerializer)
    def get_product(self, cart_item):
        return ProductSerializer(cart_item.product).data

    @extend_schema_field(serializers.IntegerField)
    def get_cart_id(self, cart_item) -> int:
        cart = cart_item.cart
        return cart.id

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


class CartItemCreateSerializer(BaseExpandSerializer):
    cart = serializers.SerializerMethodField("get_cart_id")

    @extend_schema_field(serializers.IntegerField)
    def get_cart_id(self, cart_item) -> int:
        cart = self.context.get("cart")
        return cart.id

    class Meta:
        model = CartItem
        fields = ("id", "cart", "product", "quantity")

    def create(self, validated_data):
        cart = self.context.get("cart")
        try:
            cart_item = CartItem.objects.get(
                cart=cart, product=validated_data["product"]
            )
            cart_item.quantity += validated_data["quantity"]
            cart_item.save()
            return cart_item
        except CartItem.DoesNotExist:
            cart_item = CartItem.objects.create(cart=cart, **validated_data)
            return cart_item


class CartSerializer(BaseExpandSerializer):
    cart_items = serializers.SerializerMethodField("get_cart_items")
    total_price = MoneyField(max_digits=11, decimal_places=2, read_only=True)
    total_discount_value = MoneyField(max_digits=11, decimal_places=2, read_only=True)
    total_vat_value = MoneyField(max_digits=11, decimal_places=2, read_only=True)

    @extend_schema_field(serializers.ListSerializer(child=CartItemSerializer()))
    def get_cart_items(self, cart: Cart):
        qs = CartItem.objects.filter(cart=cart)
        serializer = CartItemSerializer(qs, many=True, context=self.context)
        return serializer.data

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
        )
