from django.core.cache import cache
from django.utils.translation import gettext_lazy as _
from djmoney.contrib.django_rest_framework import MoneyField
from drf_spectacular.helpers import lazy_serializer
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from cart.models import CartItem
from product.serializers.product import ProductSerializer

# Cache TTL (seconds) for per-category recommendation results.
# A 5-minute window eliminates the per-cart-item query storm while
# keeping product ranking reasonably fresh.  Cache key format:
# ``cart_recs:cat:{category_id}``.
_CART_RECS_TTL = 300


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

    def get_cart_id(self, obj: CartItem) -> str:
        # Returns the cart's UUID — the public identifier the storefront
        # echoes back in X-Cart-Id (M18 in MULTI_TENANT_AUDIT.md). The
        # integer PK stays internal.
        return str(obj.cart.uuid)

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
        category = obj.product.category
        if not category:
            return []

        cache_key = f"cart_recs:cat:{category.pk}"

        def _fetch():
            return list(
                category.products.filter(active=True)
                .exclude(id=obj.product.id)
                .order_by("-view_count")
                .values_list("id", flat=True)[:3]
            )

        # Cache stores product IDs only; serialization happens outside
        # the cache so the response context (request, language) is
        # always applied fresh.  IDs are cheap (~24 bytes each) and
        # category-scoped, so collisions between concurrent requests
        # for different cart items in the same category are safe.
        product_ids = cache.get_or_set(cache_key, _fetch, _CART_RECS_TTL)

        from product.models.product import Product

        products = Product.objects.filter(id__in=product_ids).exclude(
            id=obj.product.id
        )
        return ProductSerializer(products, many=True, context=self.context).data

    class Meta(CartItemSerializer.Meta):
        fields = (
            *CartItemSerializer.Meta.fields,
            "recommendations",
        )


class CartItemWriteSerializer(serializers.ModelSerializer[CartItem]):
    def validate_quantity(self, value: int) -> int:
        if value <= 0:
            raise serializers.ValidationError(
                _("Quantity must be greater than zero.")
            )
        if value > 999:
            raise serializers.ValidationError(_("Quantity cannot exceed 999."))
        return value

    def validate(self, attrs: dict) -> dict:
        if self.instance:
            product = self.instance.product
            quantity = attrs.get("quantity", self.instance.quantity)
        else:
            product = attrs.get("product")
            quantity = attrs.get("quantity", 1)

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

        return attrs

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


class CartItemCreateSerializer(serializers.ModelSerializer[CartItem]):
    def validate_quantity(self, value: int) -> int:
        if value <= 0:
            raise serializers.ValidationError(
                _("Quantity must be greater than zero.")
            )
        if value > 999:
            raise serializers.ValidationError(_("Quantity cannot exceed 999."))
        return value

    def validate(self, attrs: dict) -> dict:
        product = attrs.get("product")
        quantity = attrs.get("quantity", 1)

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
        return attrs

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
            existing_item.quantity = new_quantity
            existing_item.save()
            return existing_item
        else:
            return CartItem.objects.create(
                cart=cart, product=product, quantity=quantity
            )

    class Meta:
        model = CartItem
        fields = ("product", "quantity")


class CartItemUpdateSerializer(serializers.ModelSerializer[CartItem]):
    def validate_quantity(self, value: int) -> int:
        if value <= 0:
            raise serializers.ValidationError(
                _("Quantity must be greater than zero.")
            )
        if value > 999:
            raise serializers.ValidationError(_("Quantity cannot exceed 999."))
        return value

    def validate(self, attrs: dict) -> dict:
        instance = self.instance
        if instance:
            product = instance.product
            quantity = attrs.get("quantity", instance.quantity)

            if product and product.stock < quantity:
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
        return attrs

    def update(self, instance: CartItem, validated_data: dict) -> CartItem:
        quantity = validated_data.get("quantity", instance.quantity)
        instance.quantity = quantity
        instance.save()
        return instance

    class Meta:
        model = CartItem
        fields = ("quantity",)
