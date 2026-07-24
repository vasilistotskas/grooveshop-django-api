from djmoney.contrib.django_rest_framework import MoneyField
from drf_spectacular.utils import extend_schema_field
from parler_rest.serializers import TranslatableModelSerializer
from rest_framework import serializers

from product.models.product import Product
from product.serializers.product import TranslatedFieldsFieldExtend
from product.serializers.product_attribute import ProductAttributeSerializer


class ProductVariantSerializer(
    TranslatableModelSerializer, serializers.ModelSerializer[Product]
):
    """
    A single sibling product within a variant group, trimmed to what a
    storefront swatch card needs: identity, image, price and its
    variant-axis attribute values (Colour, Memory, …).
    """

    translations = TranslatedFieldsFieldExtend(shared_model=Product)
    price = MoneyField(max_digits=11, decimal_places=2, read_only=True)
    final_price = MoneyField(max_digits=11, decimal_places=2, read_only=True)
    attribute_values = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = (
            "id",
            "translations",
            "slug",
            "active",
            "stock",
            "price",
            "final_price",
            "discount_percent",
            "main_image_path",
            "attribute_values",
        )
        read_only_fields = fields

    @extend_schema_field(ProductAttributeSerializer(many=True))
    def get_attribute_values(self, obj: Product):
        """Only the variant-axis values; filtered in-memory over the
        prefetched ``product_attributes`` to avoid extra queries."""
        variant_attrs = [
            pa
            for pa in obj.product_attributes.all()
            if pa.attribute_value.attribute.is_variant
        ]
        return ProductAttributeSerializer(
            variant_attrs, many=True, context=self.context
        ).data


class VariantAxisValueSerializer(serializers.Serializer):
    """One selectable value on a variant axis (an ``AttributeValue``)."""

    id = serializers.IntegerField()
    value = serializers.CharField()


class VariantAxisSerializer(serializers.Serializer):
    """A variant axis (an ``Attribute`` flagged ``is_variant``) plus the
    distinct values present across the group, both ordered by ``sort_order``."""

    id = serializers.IntegerField()
    name = serializers.CharField()
    values = VariantAxisValueSerializer(many=True)


class ProductVariantsResponseSerializer(serializers.Serializer):
    """Payload for ``GET /product/{id}/variants`` — the axes to render and the
    sibling products that fill them."""

    axes = VariantAxisSerializer(many=True)
    variants = ProductVariantSerializer(many=True)
