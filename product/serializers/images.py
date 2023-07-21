from typing import Dict
from typing import Type

from rest_framework import serializers
from rest_framework.relations import PrimaryKeyRelatedField

from core.api.serializers import BaseExpandSerializer
from product.models.images import ProductImages
from product.models.product import Product
from product.serializers.product import ProductSerializer


class ProductImagesSerializer(BaseExpandSerializer):
    product = PrimaryKeyRelatedField(queryset=Product.objects.all())

    class Meta:
        model = ProductImages
        fields = (
            "id",
            "title",
            "product",
            "image",
            "thumbnail",
            "is_main",
            "product_image_absolute_url",
            "product_image_filename",
            "created_at",
            "updated_at",
            "uuid",
            "sort_order",
        )

    def get_expand_fields(self) -> Dict[str, Type[serializers.ModelSerializer]]:
        return {
            "product": ProductSerializer,
        }
