from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from cart.models import Cart
from cart.models import CartItem
from product.serializers.product import ProductSerializer


class CartItemSerializer(serializers.ModelSerializer):
    cart = serializers.SerializerMethodField("get_cart_id")
    product = serializers.SerializerMethodField("get_product")

    @extend_schema_field(ProductSerializer)
    def get_product(self, cart_item) -> ProductSerializer:
        return ProductSerializer(cart_item.product).data

    @extend_schema_field(serializers.IntegerField)
    def get_cart_id(self, cart_item) -> int:
        cart = self.context.get("cart")
        return cart.id

    class Meta:
        model = CartItem
        fields = (
            "id",
            "cart",
            "product",
            "quantity",
            "total_price",
            "total_discount_value",
            "product_discount_percent",
        )


class CartItemCreateSerializer(serializers.ModelSerializer):
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
        if CartItem.objects.filter(
            cart=cart, product=validated_data["product"]
        ).exists():
            cart_item = CartItem.objects.get(
                cart=cart, product=validated_data["product"]
            )
            cart_item.quantity += validated_data["quantity"]
            cart_item.save()
            return cart_item
        cart_item = CartItem.objects.create(cart=cart, **validated_data)
        return cart_item


class CartSerializer(serializers.ModelSerializer):
    cart_items = serializers.SerializerMethodField("get_cart_items")

    @extend_schema_field(serializers.ListSerializer(child=CartItemSerializer()))
    def get_cart_items(self, cart: Cart) -> CartItemSerializer:
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
        )
