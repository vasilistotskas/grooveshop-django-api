from django.utils.translation import gettext_lazy as _
from djmoney.contrib.django_rest_framework import MoneyField
from djmoney.money import Money
from drf_spectacular.utils import extend_schema_field
from measurement.measures import Weight
from parler_rest.serializers import TranslatableModelSerializer
from rest_framework import serializers
from rest_framework.relations import PrimaryKeyRelatedField

from core.api.schema import generate_schema_multi_lang
from core.api.serializers import MeasurementSerializerField
from core.utils.serializers import TranslatedFieldExtended
from product.models.category import ProductCategory
from product.models.product import Product
from vat.models import Vat


@extend_schema_field(generate_schema_multi_lang(Product))
class TranslatedFieldsFieldExtend(TranslatedFieldExtended):
    pass


class ProductSerializer(
    TranslatableModelSerializer, serializers.ModelSerializer[Product]
):
    translations = TranslatedFieldsFieldExtend(shared_model=Product)
    category = PrimaryKeyRelatedField(queryset=ProductCategory.objects.all())
    vat = PrimaryKeyRelatedField(queryset=Vat.objects.all())
    price = MoneyField(max_digits=11, decimal_places=2)
    final_price = MoneyField(max_digits=11, decimal_places=2, read_only=True)
    discount_value = MoneyField(max_digits=11, decimal_places=2, read_only=True)
    vat_value = MoneyField(max_digits=11, decimal_places=2, read_only=True)
    weight = MeasurementSerializerField(
        measurement=Weight, required=False, allow_null=True
    )

    class Meta:
        model = Product
        fields = (
            "id",
            "translations",
            "slug",
            "category",
            "price",
            "vat",
            "view_count",
            "stock",
            "active",
            "weight",
            "seo_title",
            "seo_description",
            "seo_keywords",
            "discount_percent",
            "created_at",
            "updated_at",
            "uuid",
            "discount_value",
            "price_save_percent",
            "vat_percent",
            "vat_value",
            "final_price",
            "main_image_path",
            "review_average",
            "review_count",
            "likes_count",
            "created_at",
            "updated_at",
            "uuid",
        )

        read_only_fields = (
            "id",
            "final_price",
            "discount_value",
            "price_save_percent",
            "vat_percent",
            "vat_value",
            "final_price",
            "main_image_path",
            "review_average",
            "review_count",
            "likes_count",
            "view_count",
            "created_at",
            "updated_at",
            "uuid",
        )


class ProductDetailSerializer(ProductSerializer):
    class Meta(ProductSerializer.Meta):
        fields = (*ProductSerializer.Meta.fields,)


class ProductWriteSerializer(
    TranslatableModelSerializer, serializers.ModelSerializer[Product]
):
    category = PrimaryKeyRelatedField(queryset=ProductCategory.objects.all())
    vat = PrimaryKeyRelatedField(queryset=Vat.objects.all())
    price = MoneyField(max_digits=11, decimal_places=2)
    weight = MeasurementSerializerField(
        measurement=Weight, required=False, allow_null=True
    )
    translations = TranslatedFieldsFieldExtend(shared_model=Product)

    def validate_price(self, value: Money) -> Money:
        if value.amount <= 0:
            raise serializers.ValidationError(
                _("Price must be greater than zero.")
            )
        if value.amount > 99999:
            raise serializers.ValidationError(_("Price cannot exceed 99,999."))
        return value

    def validate_stock(self, value: int) -> int:
        if value < 0:
            raise serializers.ValidationError(_("Stock cannot be negative."))
        return value

    def validate_discount_percent(self, value: int) -> int:
        if value < 0 or value > 100:
            raise serializers.ValidationError(
                _("Discount percent must be between 0 and 100.")
            )
        return value

    def validate_slug(self, value: str) -> str:
        if not value:
            raise serializers.ValidationError(_("Slug is required."))

        queryset = Product.objects.filter(slug=value)
        if self.instance:
            queryset = queryset.exclude(pk=self.instance.pk)

        if queryset.exists():
            raise serializers.ValidationError(
                _("A post with this slug already exists.")
            )

        return value

    class Meta:
        model = Product
        fields = (
            "translations",
            "slug",
            "category",
            "price",
            "vat",
            "stock",
            "weight",
            "discount_percent",
            "seo_title",
            "seo_description",
            "seo_keywords",
            "active",
        )
