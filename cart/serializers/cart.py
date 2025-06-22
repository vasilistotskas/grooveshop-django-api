from django.utils.translation import gettext_lazy as _
from djmoney.contrib.django_rest_framework import MoneyField
from drf_spectacular.helpers import lazy_serializer
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from cart.models import Cart
from cart.serializers.item import CartItemDetailSerializer, CartItemSerializer
from product.serializers.product import ProductSerializer


class CartWriteSerializer(serializers.ModelSerializer[Cart]):
    class Meta:
        model = Cart
        fields = ("user", "session_key")


class CartSerializer(serializers.ModelSerializer[Cart]):
    items = CartItemSerializer(many=True, read_only=True)
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
            "user",
            "session_key",
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


class CartDetailSerializer(CartSerializer):
    items = CartItemDetailSerializer(many=True, read_only=True)
    recommendations = serializers.SerializerMethodField(
        help_text=_("Product recommendations based on cart contents")
    )

    @extend_schema_field(
        lazy_serializer("product.serializers.product.ProductSerializer")(
            many=True
        )
    )
    def get_recommendations(self, obj: Cart):
        categories = set()
        for item in obj.items.all():
            if item.product.category:
                categories.add(item.product.category)

        if categories:
            from product.models.product import Product  # noqa: PLC0415

            recommendations = (
                Product.objects.filter(category__in=categories, active=True)
                .exclude(id__in=obj.items.values_list("product_id", flat=True))
                .order_by("-view_count")[:4]
            )

            return ProductSerializer(
                recommendations, many=True, context=self.context
            ).data
        return []

    class Meta(CartSerializer.Meta):
        fields = (
            *CartSerializer.Meta.fields,
            "items",
            "recommendations",
        )
