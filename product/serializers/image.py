import importlib
from typing import Dict
from typing import override
from typing import Type

from drf_spectacular.utils import extend_schema_field
from parler_rest.serializers import TranslatableModelSerializer
from rest_framework import serializers
from rest_framework.relations import PrimaryKeyRelatedField

from core.api.schema import generate_schema_multi_lang
from core.api.serializers import BaseExpandSerializer
from core.utils.serializers import TranslatedFieldExtended
from product.models.image import ProductImage
from product.models.product import Product


@extend_schema_field(generate_schema_multi_lang(ProductImage))
class TranslatedFieldsFieldExtend(TranslatedFieldExtended):
    pass


class ProductImageSerializer(TranslatableModelSerializer, BaseExpandSerializer):
    product = PrimaryKeyRelatedField(queryset=Product.objects.all())
    translations = TranslatedFieldsFieldExtend(shared_model=ProductImage)

    class Meta:
        model = ProductImage
        fields = (
            "translations",
            "id",
            "product",
            "image",
            "thumbnail",
            "is_main",
            "sort_order",
            "created_at",
            "updated_at",
            "uuid",
            "main_image_path",
        )
        read_only_fields = (
            "created_at",
            "updated_at",
            "uuid",
            "main_image_path",
        )

    @override
    def get_expand_fields(
        self,
    ) -> Dict[str, Type[serializers.ModelSerializer]]:
        product_serializer = importlib.import_module("product.serializers.product").ProductSerializer
        return {
            "product": product_serializer,
        }
