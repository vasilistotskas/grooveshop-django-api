from typing import Dict
from typing import Type

from core.api.serializers import BaseExpandSerializer
from product.models.category import ProductCategory
from product.models.product import Product
from product.models.product import ProductImages
from product.serializers.category import ProductCategorySerializer
from vat.models import Vat
from vat.serializers import VatSerializer
from rest_framework import serializers
from rest_framework.relations import PrimaryKeyRelatedField


class ProductSerializer(BaseExpandSerializer):
    category = PrimaryKeyRelatedField(queryset=ProductCategory.objects.all())
    vat = PrimaryKeyRelatedField(queryset=Vat.objects.all())

    class Meta:
        model = Product
        fields = (
            "id",
            "name",
            "slug",
            "category",
            "absolute_url",
            "description",
            "price",
            "vat",
            "vat_percent",
            "vat_value",
            "final_price",
            "hits",
            "likes_counter",
            "stock",
            "active",
            "weight",
            "seo_title",
            "seo_description",
            "seo_keywords",
            "uuid",
            "discount_percent",
            "discount_value",
            "price_save_percent",
            "created_at",
            "updated_at",
            "main_image_absolute_url",
            "main_image_filename",
            "review_average",
            "review_counter",
        )

    def get_expand_fields(self) -> Dict[str, Type[serializers.ModelSerializer]]:
        return {"category": ProductCategorySerializer, "vat": VatSerializer}


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
