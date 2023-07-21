from typing import Dict
from typing import Type

from drf_spectacular.utils import extend_schema_field
from parler_rest.fields import TranslatedFieldsField
from parler_rest.serializers import TranslatableModelSerializer
from rest_framework import serializers
from rest_framework.relations import PrimaryKeyRelatedField

from core.api.schema import generate_schema_multi_lang
from core.api.serializers import BaseExpandSerializer
from product.models.category import ProductCategory
from product.models.product import Product
from product.serializers.category import ProductCategorySerializer
from vat.models import Vat
from vat.serializers import VatSerializer


@extend_schema_field(generate_schema_multi_lang(Product))
class TranslatedFieldsFieldExtend(TranslatedFieldsField):
    pass


class ProductSerializer(TranslatableModelSerializer, BaseExpandSerializer):
    translations = TranslatedFieldsFieldExtend(shared_model=Product)
    category = PrimaryKeyRelatedField(queryset=ProductCategory.objects.all())
    vat = PrimaryKeyRelatedField(queryset=Vat.objects.all())

    class Meta:
        model = Product
        fields = (
            "translations",
            "id",
            "slug",
            "category",
            "absolute_url",
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
