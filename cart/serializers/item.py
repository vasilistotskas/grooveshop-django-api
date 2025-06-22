from django.utils.translation import gettext_lazy as _
from djmoney.contrib.django_rest_framework import MoneyField
from drf_spectacular.helpers import lazy_serializer
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from cart.models import CartItem
from product.serializers.product import ProductSerializer


class CartItemSerializer(serializers.ModelSerializer[CartItem]):
    cart_id = serializers.SerializerMethodField()
    product = ProductSerializer(read_only=True)
    weight_info = serializers.SerializerMethodField(
        help_text=_("Weight information for shipping calculations")
    )
    price = MoneyField(max_digits=11, decimal_places=2, read_only=True)
    final_price = MoneyField(max_digits=11, decimal_places=2, read_only=True)
    discount_value = MoneyField(max_digits=11, decimal_places=2, read_only=True)
    vat_value = MoneyField(max_digits=11, decimal_places=2, read_only=True)
    total_price = MoneyField(max_digits=11, decimal_places=2, read_only=True)
    total_discount_value = MoneyField(
        max_digits=11, decimal_places=2, read_only=True
    )

    def get_cart_id(self, obj: CartItem) -> int:
        return obj.cart.id

    @extend_schema_field(
        {
            "type": "object",
            "properties": {
                "unit_weight": {"type": "number"},
                "total_weight": {"type": "number"},
                "weight_unit": {"type": "string"},
            },
            "required": ["unit_weight", "total_weight", "weight_unit"],
        }
    )
    def get_weight_info(self, obj: CartItem) -> dict | None:
        product = obj.product
        if product.weight:
            total_weight = product.weight.value * obj.quantity
            return {
                "unit_weight": product.weight.value,
                "total_weight": total_weight,
                "weight_unit": product.weight.unit,
            }
        return None

    class Meta:
        model = CartItem
        fields = (
            "id",
            "cart_id",
            "product",
            "quantity",
            "weight_info",
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
            "id",
            "cart_id",
            "product",
            "weight_info",
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


class CartItemDetailSerializer(CartItemSerializer):
    recommendations = serializers.SerializerMethodField(
        help_text=_("Related products that might interest the customer")
    )

    @extend_schema_field(
        lazy_serializer("product.serializers.product.ProductSerializer")(
            many=True
        )
    )
    def get_recommendations(self, obj: CartItem):
        if obj.product.category:
            related_products = (
                obj.product.category.products.filter(active=True)
                .exclude(id=obj.product.id)
                .order_by("-view_count")[:3]
            )

            return ProductSerializer(
                related_products, many=True, context=self.context
            ).data
        return []

    class Meta(CartItemSerializer.Meta):
        fields = (
            *CartItemSerializer.Meta.fields,
            "recommendations",
        )


class CartItemWriteSerializer(serializers.ModelSerializer[CartItem]):
    def validate_quantity(self, value: int):
        if value <= 0:
            raise serializers.ValidationError(
                _("Quantity must be greater than zero.")
            )
        if value > 999:
            raise serializers.ValidationError(_("Quantity cannot exceed 999."))
        return value

    def validate(self, data: dict) -> dict:
        if self.instance:
            product = self.instance.product
            quantity = data.get("quantity", self.instance.quantity)
        else:
            product = data.get("product")
            quantity = data.get("quantity", 1)

        if product:
            if not product.active:
                raise serializers.ValidationError(
                    _("Product '{product_name}' is not available.").format(
                        product_name=product.safe_translation_getter(
                            "name", any_language=True
                        ),
                    )
                )

            if product.stock < quantity:
                raise serializers.ValidationError(
                    _(
                        "Not enough stock for '{product_name}'. Available: {product_stock}, Requested: {quantity}"
                    ).format(
                        product_name=product.safe_translation_getter(
                            "name", any_language=True
                        ),
                        product_stock=product.stock,
                        quantity=quantity,
                    )
                )

        return data

    def create(self, validated_data: dict) -> CartItem:
        cart = self.context.get("cart") or validated_data.get("cart")
        product = validated_data.get("product")
        quantity = validated_data.get("quantity")

        if not cart:
            raise serializers.ValidationError(_("Cart is not provided."))

        existing_item = CartItem.objects.filter(
            cart=cart, product=product
        ).first()

        if existing_item:
            new_quantity = existing_item.quantity + quantity
            if product.stock < new_quantity:
                raise serializers.ValidationError(
                    _(
                        "Not enough stock to add {quantity} more. Current in cart: {existing_item_quantity}, Available: {product_stock}"
                    ).format(
                        quantity=quantity,
                        existing_item_quantity=existing_item.quantity,
                        product_stock=product.stock,
                    )
                )
            existing_item.quantity = new_quantity
            existing_item.save()
            return existing_item
        else:
            return CartItem.objects.create(
                cart=cart, product=product, quantity=quantity
            )

    class Meta:
        model = CartItem
        fields = ("id", "cart", "product", "quantity")
        read_only_fields = ("id",)
