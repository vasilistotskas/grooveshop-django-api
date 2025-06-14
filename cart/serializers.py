from django.utils.translation import gettext_lazy as _
from djmoney.contrib.django_rest_framework import MoneyField
from rest_framework import serializers

from cart.models import Cart, CartItem
from product.serializers.product import ProductListSerializer


class CartItemListSerializer(serializers.ModelSerializer[CartItem]):
    cart = serializers.SerializerMethodField()
    product = ProductListSerializer(read_only=True)
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

    def get_cart(self, obj: CartItem) -> int:
        return obj.cart.id

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
            "cart",
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
            "cart",
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


class CartItemDetailSerializer(CartItemListSerializer):
    recommendations = serializers.SerializerMethodField(
        help_text=_("Related products that might interest the customer")
    )

    def get_recommendations(self, obj: CartItem) -> list:
        if obj.product.category:
            related_products = (
                obj.product.category.products.filter(active=True)
                .exclude(id=obj.product.id)
                .order_by("-view_count")[:3]
            )

            return [
                {
                    "id": product.id,
                    "name": product.safe_translation_getter(
                        "name", any_language=True
                    ),
                    "slug": product.slug,
                    "price": product.final_price.amount,
                    "currency": product.final_price.currency.code,
                    "image": product.main_image_path,
                }
                for product in related_products
            ]
        return []

    class Meta(CartItemListSerializer.Meta):
        fields = (
            *CartItemListSerializer.Meta.fields,
            "recommendations",
        )


class CartItemWriteSerializer(serializers.ModelSerializer[CartItem]):
    @staticmethod
    def validate_quantity(value: int):
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


class CartWriteSerializer(serializers.ModelSerializer[Cart]):
    class Meta:
        model = Cart
        fields = ("user", "session_key")


class CartListSerializer(serializers.ModelSerializer[Cart]):
    items = CartItemListSerializer(many=True, read_only=True)
    total_price = MoneyField(max_digits=11, decimal_places=2, read_only=True)
    total_discount_value = MoneyField(
        max_digits=11, decimal_places=2, read_only=True
    )
    total_vat_value = MoneyField(
        max_digits=11, decimal_places=2, read_only=True
    )

    class Meta:
        model = Cart
        fields = (
            "id",
            "uuid",
            "total_price",
            "total_discount_value",
            "total_vat_value",
            "total_items",
            "total_items_unique",
            "created_at",
            "updated_at",
            "last_activity",
        )
        read_only_fields = (
            "id",
            "uuid",
            "total_price",
            "total_discount_value",
            "total_vat_value",
            "total_items",
            "total_items_unique",
            "created_at",
            "updated_at",
            "last_activity",
        )


class CartDetailSerializer(CartListSerializer):
    items = CartItemDetailSerializer(many=True, read_only=True)
    recommendations = serializers.SerializerMethodField(
        help_text=_("Product recommendations based on cart contents")
    )

    def get_recommendations(self, obj: Cart) -> list:
        categories = set()
        for item in obj.items.all():
            if item.product.category:
                categories.add(item.product.category)

        if categories:
            from product.models.product import Product

            recommendations = (
                Product.objects.filter(category__in=categories, active=True)
                .exclude(id__in=obj.items.values_list("product_id", flat=True))
                .order_by("-view_count")[:4]
            )

            return [
                {
                    "id": product.id,
                    "name": product.safe_translation_getter(
                        "name", any_language=True
                    ),
                    "slug": product.slug,
                    "price": product.final_price.amount,
                    "currency": product.final_price.currency.code,
                    "image": product.main_image_path,
                    "category": product.category.safe_translation_getter(
                        "name", any_language=True
                    ),
                }
                for product in recommendations
            ]
        return []

    class Meta(CartListSerializer.Meta):
        fields = (
            *CartListSerializer.Meta.fields,
            "items",
            "recommendations",
        )
