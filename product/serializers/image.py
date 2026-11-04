import os

from django.db import models
from drf_spectacular.utils import extend_schema_field
from parler_rest.serializers import TranslatableModelSerializer
from rest_framework import serializers
from rest_framework.relations import PrimaryKeyRelatedField

from core.api.image_field import ServableImageField
from core.api.schema import generate_schema_multi_lang
from core.utils.serializers import TranslatedFieldExtended
from product.models.image import ProductImage
from product.models.product import Product
from product.serializers.product import ProductSerializer


@extend_schema_field(generate_schema_multi_lang(ProductImage))
class TranslatedFieldsFieldExtend(TranslatedFieldExtended):
    pass


class ProductImageSerializer(
    TranslatableModelSerializer, serializers.ModelSerializer[ProductImage]
):
    product = PrimaryKeyRelatedField(read_only=True)
    translations = TranslatedFieldsFieldExtend(shared_model=ProductImage)

    alt_text = serializers.SerializerMethodField()

    class Meta:
        model = ProductImage
        fields = (
            "id",
            "uuid",
            "product",
            "image",
            "alt_text",
            "is_main",
            "sort_order",
            "translations",
            "main_image_path",
            "created_at",
        )
        read_only_fields = (
            "id",
            "uuid",
            "product",
            "sort_order",
            "alt_text",
            "main_image_path",
            "created_at",
        )

    def get_alt_text(self, obj: ProductImage) -> str:
        """The merchant's alt text, else empty.

        No generated sentence: it would be in one language whatever the
        store speaks, so the storefront builds its own localized fallback.
        """
        return obj.safe_translation_getter("alt_text", any_language=True) or ""


class ProductImageDetailSerializer(ProductImageSerializer):
    product = serializers.SerializerMethodField()
    image_dimensions = serializers.SerializerMethodField()
    image_format = serializers.SerializerMethodField()
    usage_context = serializers.SerializerMethodField()

    class Meta(ProductImageSerializer.Meta):
        fields = (
            *ProductImageSerializer.Meta.fields,
            "image_dimensions",
            "image_format",
            "usage_context",
            "updated_at",
        )
        read_only_fields = (
            *ProductImageSerializer.Meta.read_only_fields,
            "image_dimensions",
            "image_format",
            "usage_context",
            "updated_at",
        )

    @extend_schema_field(ProductSerializer)
    def get_product(self, obj: ProductImage):
        return ProductSerializer(obj.product, context=self.context).data

    @extend_schema_field(
        {
            "type": "object",
            "properties": {
                "width": {"type": "number"},
                "height": {"type": "number"},
                "aspect_ratio": {"type": "number"},
            },
        }
    )
    def get_image_dimensions(self, obj: ProductImage):
        if (
            obj.image
            and hasattr(obj.image, "width")
            and hasattr(obj.image, "height")
        ):
            return {
                "width": obj.image.width,
                "height": obj.image.height,
                "aspect_ratio": round(obj.image.width / obj.image.height, 2)
                if obj.image.height > 0
                else 0,
            }
        return {"width": 0, "height": 0, "aspect_ratio": 0}

    def get_image_format(self, obj: ProductImage) -> str:
        if obj.image and hasattr(obj.image, "name"):
            return (
                os.path.splitext(str(obj.image.name))[1]
                .lower()
                .replace(".", "")
            )
        return ""

    @extend_schema_field(
        {
            "type": "object",
            "properties": {
                "is_main": {"type": "boolean"},
                "position_in_gallery": {"type": "number"},
                "total_product_images": {"type": "number"},
                "recommended_for": {"type": "string"},
            },
        }
    )
    def get_usage_context(self, obj: ProductImage):
        return {
            "is_main": obj.is_main,
            "position_in_gallery": obj.sort_order,
            "total_product_images": obj.product.images.count()
            if obj.product_id
            else 0,
            "recommended_for": "thumbnail" if obj.is_main else "gallery",
        }


#: The formats a product image may be (contract names).
PRODUCT_IMAGE_FORMATS = ("jpeg", "png", "webp", "avif", "svg")


class ProductImageWriteSerializer(
    TranslatableModelSerializer, serializers.ModelSerializer[ProductImage]
):
    product = PrimaryKeyRelatedField(queryset=Product.objects.all())
    # A product shows a still: no GIF or TIFF, whatever the media service
    # could serve (the size and pixel rules are the shared ones).
    image = ServableImageField(
        formats=PRODUCT_IMAGE_FORMATS,
        label=ProductImage._meta.get_field("image").verbose_name,
    )
    translations = TranslatedFieldsFieldExtend(shared_model=ProductImage)

    class Meta:
        model = ProductImage
        fields = (
            "product",
            "image",
            "is_main",
            "sort_order",
            "translations",
        )
        read_only_fields = ("sort_order",)
        # DRF >= 3.18.2 turns the conditional ``unique_main_product_image``
        # constraint into a validator that rejects a second ``is_main`` image.
        # The contract here is promotion, not rejection: ``ProductImage.save``
        # demotes the previous main image, and the constraint stays as the
        # database-level safety net.
        validators = []

    def validate(self, attrs):
        product = attrs.get("product")
        is_main = attrs.get("is_main", False)

        if is_main and product:
            existing_main = ProductImage.objects.filter(
                product=product, is_main=True
            ).exclude(pk=self.instance.pk if self.instance else None)

            if existing_main.exists():
                pass

        return attrs

    def create(self, validated_data):
        product = validated_data["product"]

        if "sort_order" not in validated_data:
            max_order = ProductImage.objects.filter(product=product).aggregate(
                max_order=models.Max("sort_order")
            )["max_order"]
            validated_data["sort_order"] = (max_order or 0) + 1

        return super().create(validated_data)
