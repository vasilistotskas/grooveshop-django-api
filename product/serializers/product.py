import importlib
from typing import Dict
from typing import Type

from djmoney.contrib.django_rest_framework import MoneyField
from drf_spectacular.utils import extend_schema_field
from measurement.measures import Weight
from parler_rest.fields import TranslatedFieldsField
from parler_rest.serializers import TranslatableModelSerializer
from rest_framework import serializers
from rest_framework.relations import PrimaryKeyRelatedField

from core.api.schema import generate_schema_multi_lang
from core.api.serializers import BaseExpandSerializer
from core.serializers import MeasurementSerializerField
from product.models.category import ProductCategory
from product.models.product import Product
from vat.models import Vat


@extend_schema_field(generate_schema_multi_lang(Product))
class TranslatedFieldsFieldExtend(TranslatedFieldsField):
    pass


class ProductSerializer(TranslatableModelSerializer, BaseExpandSerializer):
    translations = TranslatedFieldsFieldExtend(shared_model=Product)
    category = PrimaryKeyRelatedField(queryset=ProductCategory.objects.all())
    vat = PrimaryKeyRelatedField(queryset=Vat.objects.all())
    price = MoneyField(max_digits=11, decimal_places=2)
    final_price = MoneyField(max_digits=11, decimal_places=2, read_only=True)
    discount_value = MoneyField(max_digits=11, decimal_places=2, read_only=True)
    vat_value = MoneyField(max_digits=11, decimal_places=2, read_only=True)
    weight = MeasurementSerializerField(measurement=Weight, required=False, allow_null=True)

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
            "view_count",
            "likes_count",
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
            "review_count",
        )

    def get_expand_fields(
        self,
    ) -> Dict[str, Type[serializers.ModelSerializer]]:
        product_category_serializer = importlib.import_module("product.serializers.category").ProductCategorySerializer
        vat_serializer = importlib.import_module("vat.serializers").VatSerializer
        return {"category": product_category_serializer, "vat": vat_serializer}
