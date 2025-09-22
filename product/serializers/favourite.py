from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.relations import PrimaryKeyRelatedField

from core.api.schema import generate_schema_multi_lang
from core.utils.serializers import TranslatedFieldExtended
from product.models.favourite import ProductFavourite
from product.models.product import Product
from product.serializers.product import (
    ProductDetailSerializer,
)

User = get_user_model()


@extend_schema_field(generate_schema_multi_lang(Product))
class TranslatedFieldsFieldExtend(TranslatedFieldExtended):
    pass


class ProductFavouriteSerializer(serializers.ModelSerializer[ProductFavourite]):
    user_id = serializers.IntegerField(source="user.id", read_only=True)
    user_username = serializers.CharField(
        source="user.username", read_only=True
    )
    product = ProductDetailSerializer(read_only=True)

    class Meta:
        model = ProductFavourite
        fields = (
            "id",
            "user_id",
            "user_username",
            "product",
            "created_at",
            "uuid",
        )


class ProductFavouriteDetailSerializer(ProductFavouriteSerializer):
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
        fields = (
            "id",
            "product",
        )
        read_only_fields = ("id",)

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
