from django.db.models import Count
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
from product.enum.review import RateEnum
from product.models.brand import Brand
from product.models.category import ProductCategory
from product.models.product import Product
from product.serializers.product_attribute import ProductAttributeSerializer
from promotion.enum import OfferKind
from vat.models import Vat


@extend_schema_field(generate_schema_multi_lang(Product))
class TranslatedFieldsFieldExtend(TranslatedFieldExtended):
    pass


class ProductSerializer(
    TranslatableModelSerializer, serializers.ModelSerializer[Product]
):
    translations = TranslatedFieldsFieldExtend(shared_model=Product)
    category = PrimaryKeyRelatedField(queryset=ProductCategory.objects.all())
    # Mirrors Product.vat (null=True, blank=True, SET_NULL). An
    # explicitly-declared relation does NOT inherit the model's
    # nullability, so without this the field is required — and on a
    # tenant with no Vat rows the whole product write API rejects every
    # payload, because the queryset is empty too.
    vat = PrimaryKeyRelatedField(
        queryset=Vat.objects.all(), required=False, allow_null=True
    )
    brand_name = serializers.CharField(
        source="brand.name", read_only=True, allow_null=True
    )
    price = MoneyField(max_digits=11, decimal_places=2)
    final_price = MoneyField(max_digits=11, decimal_places=2, read_only=True)
    discount_value = MoneyField(max_digits=11, decimal_places=2, read_only=True)
    vat_value = MoneyField(max_digits=11, decimal_places=2, read_only=True)
    weight = MeasurementSerializerField(
        measurement=Weight, required=False, allow_null=True
    )
    attributes = ProductAttributeSerializer(
        source="product_attributes", many=True, read_only=True
    )
    # Reads the ``with_offer_kind()`` annotation and nothing else: a
    # product nested in a cart, order or favourite line comes from a
    # queryset without it, and resolving it per row would add a query to
    # every line of surfaces that show no offer badge. Those payloads
    # carry ``null``; the catalogue list and detail annotate.
    # ``required=False``, NOT ``read_only=True``: drf-spectacular marks
    # every read-only field as always present, and a storefront that
    # deploys before the API would then reject the old payload.
    offer_kind = serializers.ChoiceField(
        source="offer_kind_annotation",
        choices=OfferKind.choices,
        allow_null=True,
        required=False,
    )

    class Meta:
        model = Product
        fields = (
            "id",
            "translations",
            "slug",
            "category",
            "variant_group",
            "brand",
            "brand_name",
            "price",
            "vat",
            "view_count",
            "stock",
            "low_stock_threshold",
            "active",
            "weight",
            "discount_percent",
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
            "attributes",
            "offer_kind",
        )

        read_only_fields = (
            "id",
            "variant_group",
            "brand",
            "brand_name",
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
            "low_stock_threshold",
            "created_at",
            "updated_at",
            "uuid",
            "attributes",
        )


class RatingDistributionSerializer(serializers.Serializer):
    rate = serializers.IntegerField()
    count = serializers.IntegerField()


class ProductDetailSerializer(ProductSerializer):
    class Meta(ProductSerializer.Meta):
        fields = (*ProductSerializer.Meta.fields, "price_drop_alerts_enabled")
        read_only_fields = (
            *ProductSerializer.Meta.read_only_fields,
            "price_drop_alerts_enabled",
        )


class ProductRetrieveSerializer(ProductDetailSerializer):
    """The product page's own payload.

    ``ProductDetailSerializer`` is also nested in list payloads
    (favourites), where the distribution query would run once per row,
    so the extra field lives on this single-object subclass.
    """

    rating_distribution = serializers.SerializerMethodField()

    class Meta(ProductDetailSerializer.Meta):
        fields = (*ProductDetailSerializer.Meta.fields, "rating_distribution")
        read_only_fields = (
            *ProductDetailSerializer.Meta.read_only_fields,
            "rating_distribution",
        )

    @extend_schema_field(RatingDistributionSerializer(many=True))
    def get_rating_distribution(self, obj: Product) -> list[dict[str, int]]:
        """Approved reviews per rate, one row for every ``RateEnum`` value.

        The scale is ``RateEnum`` (1-10), not five stars; rates nobody
        used come back as ``count: 0`` so the client never has to fill
        gaps. Approved only, like the public review list: a pending or
        rejected review must not move the bars.
        """
        counts = dict(
            obj.reviews.get_queryset()
            .approved()
            .order_by()
            .values_list("rate")
            .annotate(count=Count("pk"))
        )
        return [
            {"rate": rate, "count": counts.get(rate, 0)}
            for rate in RateEnum.values
        ]


class ProductWriteSerializer(
    TranslatableModelSerializer, serializers.ModelSerializer[Product]
):
    category = PrimaryKeyRelatedField(queryset=ProductCategory.objects.all())
    # See ProductSerializer.vat — matches the model's nullability so a
    # tenant without seeded VAT rates can still create products.
    vat = PrimaryKeyRelatedField(
        queryset=Vat.objects.all(), required=False, allow_null=True
    )
    brand = PrimaryKeyRelatedField(
        queryset=Brand.objects.all(), required=False, allow_null=True
    )
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
            "brand",
            "price",
            "vat",
            "stock",
            "weight",
            "discount_percent",
            "active",
        )
