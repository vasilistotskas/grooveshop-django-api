from django.utils.translation import gettext_lazy as _
from djmoney.contrib.django_rest_framework import MoneyField
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from cart.models import Cart, CartItem
from product.serializers.product import ProductSerializer


class CartItemSerializer(serializers.ModelSerializer):
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


class CartItemCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = CartItem
        fields = ("id", "cart", "product", "quantity")
        read_only_fields = ("id",)

    def create(self, validated_data):
        cart = self.context.get("cart")
        product = validated_data.get("product")
        quantity = validated_data.get("quantity")

        if not cart:
            raise serializers.ValidationError(_("Cart is not provided."))

        existing_item = CartItem.objects.filter(
            cart=cart, product=product
        ).first()

        if existing_item:
            existing_item.quantity += quantity
            existing_item.save()
            return existing_item
        else:
            return CartItem.objects.create(
                cart=cart, product=product, quantity=quantity
            )


class CartSerializer(serializers.ModelSerializer):
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
            "session_key",
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
            "session_key",
            "total_price",
            "total_discount_value",
            "total_vat_value",
            "total_items",
            "total_items_unique",
            "created_at",
            "updated_at",
            "uuid",
        )
