from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.relations import PrimaryKeyRelatedField

from product.models.favourite import ProductFavourite
from product.models.product import Product
from product.serializers.product import (
    ProductDetailSerializer,
)

User = get_user_model()


class ProductFavouriteSerializer(serializers.ModelSerializer[ProductFavourite]):
    user_id = serializers.IntegerField(source="user.id", read_only=True)
    user_username = serializers.CharField(
        source="user.username", read_only=True
    )
    product_name = serializers.SerializerMethodField(read_only=True)
    product_price = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = ProductFavourite
        fields = (
            "id",
            "user_id",
            "user_username",
            "product",
            "product_name",
            "product_price",
            "created_at",
            "uuid",
        )

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_product_name(self, obj) -> str:
        if obj.product:
            return obj.product.safe_translation_getter(
                "name", any_language=True
            )
        return None

    @extend_schema_field(
        serializers.DecimalField(
            max_digits=10, decimal_places=2, allow_null=True
        )
    )
    def get_product_price(self, obj):
        if obj.product and hasattr(obj.product, "price"):
            return (
                float(obj.product.price.amount) if obj.product.price else None
            )
        return None


class ProductFavouriteDetailSerializer(ProductFavouriteSerializer):
    product = ProductDetailSerializer(read_only=True)
    user = serializers.StringRelatedField(read_only=True)

    class Meta(ProductFavouriteSerializer.Meta):
        fields = (
            *ProductFavouriteSerializer.Meta.fields,
            "user",
            "updated_at",
        )


class ProductFavouriteWriteSerializer(
    serializers.ModelSerializer[ProductFavourite]
):
    product = PrimaryKeyRelatedField(queryset=Product.objects.all(), many=False)

    class Meta:
        model = ProductFavourite
        fields = ("product",)

    def validate(self, attrs):
        user = self.context["request"].user
        product = attrs.get("product")

        if (
            not self.instance
            and ProductFavourite.objects.filter(
                user=user, product=product
            ).exists()
        ):
            raise serializers.ValidationError(
                _("Product is already in favorites")
            )

        return attrs


class ProductFavouriteByProductsRequestSerializer(serializers.Serializer):
    product_ids = serializers.ListField(
        child=serializers.IntegerField(),
        help_text=_("List of product IDs to check for favorites"),
    )


class ProductFavouriteByProductsResponseSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    user_id = serializers.IntegerField()
    product_id = serializers.IntegerField()
    created_at = serializers.DateTimeField()


class ProductDetailResponseSerializer(ProductDetailSerializer):
    class Meta(ProductDetailSerializer.Meta):
        pass
