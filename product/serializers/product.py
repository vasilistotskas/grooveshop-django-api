from functools import cached_property

from django.db import connection
from django.db.models import Count
from django.utils.translation import gettext_lazy as _
from djmoney.contrib.django_rest_framework import MoneyField
from djmoney.money import Money
from drf_spectacular.utils import (
    extend_schema_field,
)
from measurement.measures import Weight
from parler_rest.serializers import TranslatableModelSerializer
from parler_rest.utils import create_translated_fields_serializer
from rest_framework import serializers
from rest_framework.fields import get_attribute
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
from tenant.features import feature_on
from vat.models import Vat


@extend_schema_field(generate_schema_multi_lang(Product))
class TranslatedFieldsFieldExtend(TranslatedFieldExtended):
    pass


# What a product card, the sitemap and the RSS feed read of a
# translation; the SEO copy belongs to the product page.
LIST_TRANSLATED_FIELDS = ("name", "description")


@extend_schema_field(
    generate_schema_multi_lang(Product, fields=LIST_TRANSLATED_FIELDS)
)
class ListTranslatedFieldsField(TranslatedFieldExtended):
    pass


class AnnouncedReductionStartField(serializers.DateTimeField):
    """When the reduction in force began, only while one may be announced.

    ``Product.reduction_started_at`` is also set while a reduction
    exists but announces nothing (no 30-day reference), so it is read
    together with ``prior_price``: the date is shown exactly when the
    prior price is, and ``null`` otherwise. Reading both attributes by
    name lets a model instance and an agent row dict share it.
    """

    def __init__(self, **kwargs):
        kwargs.setdefault("read_only", True)
        kwargs.setdefault("allow_null", True)
        kwargs["source"] = "*"
        super().__init__(**kwargs)

    def get_attribute(self, instance):
        if get_attribute(instance, ["prior_price"]) is None:
            return None
        return get_attribute(instance, ["reduction_started_at"])


class EffectivePriceFieldsSerializer(serializers.Serializer):
    """What a shopper pays and the Omnibus-lawful announcement of it.

    One definition for every serializer that shows a product's price, so
    the shapes cannot drift between the catalogue and the agent surface.
    """

    # What a consumer pays now, promotions that need nothing from the
    # basket included; ``final_price`` stops at VAT and the markdown.
    effective_price = MoneyField(
        max_digits=11, decimal_places=2, read_only=True, allow_null=True
    )
    # The EU Omnibus reference: the lowest price of the 30 days before the
    # current reduction began. ``null`` means nothing may be announced.
    prior_price = MoneyField(
        max_digits=11, decimal_places=2, read_only=True, allow_null=True
    )
    announced_discount_percent = serializers.IntegerField(
        read_only=True, allow_null=True
    )
    # When that reduction began: the start of the period ``prior_price``
    # is the reference for. ``null`` whenever ``prior_price`` is.
    reduction_started_at = AnnouncedReductionStartField()


#: The database range of ``Product.stock``, the bounds its column enforces.
_STOCK_FIELD = Product._meta.get_field("stock")
_STOCK_RANGE = connection.ops.integer_field_range(
    _STOCK_FIELD.get_internal_type()
)


# Zeroes the star average and review count of a store whose reviews are
# hidden: zero is what a product with no reviews serves, so the fields
# keep their shape. A comment, not a docstring: drf-spectacular would
# publish a docstring as the description of every serializer below it.
class HiddenReviewsMixin(serializers.Serializer):
    @cached_property
    def reviews_visible(self) -> bool:
        # One read per response: a list renders every row through the
        # same child serializer.
        return feature_on("product_reviews")

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if not self.reviews_visible:
            data["review_average"] = 0.0
            data["review_count"] = 0
        return data


class ProductCardSerializer(
    HiddenReviewsMixin,
    TranslatableModelSerializer,
    serializers.ModelSerializer[Product],
    EffectivePriceFieldsSerializer,
):
    """The product list's row: what a product card, the sitemap and the
    RSS feed read.

    A product's attributes, gallery and variant group are the product
    page's (``ProductRetrieveSerializer``) and the catalogue feed's
    (``/product/feed``, ``ProductSerializer``). Built from
    ``Product.objects.for_list()``.
    """

    translations = ListTranslatedFieldsField(
        shared_model=Product,
        serializer_class=create_translated_fields_serializer(
            Product,
            related_name="translations",
            meta={"fields": list(LIST_TRANSLATED_FIELDS)},
        ),
    )
    brand_name = serializers.CharField(
        source="brand.name", read_only=True, allow_null=True
    )
    final_price = MoneyField(max_digits=11, decimal_places=2, read_only=True)
    # The card announces a reduction by its prior price and percentage;
    # when it began is the product page's to say.
    reduction_started_at = None

    class Meta:
        model = Product
        fields = (
            "id",
            "translations",
            "slug",
            "category",
            "brand_name",
            "stock",
            "low_stock_threshold",
            "active",
            "final_price",
            "effective_price",
            "prior_price",
            "announced_discount_percent",
            "main_image_path",
            "review_average",
            "review_count",
            "created_at",
            "updated_at",
            "uuid",
        )
        read_only_fields = fields


# The merchant's markdown (``Product.discount_percent``) is an input, not an
# announcement: a "was" price struck through and a "-N%" badge built from it
# carry no prior price, which the EU Omnibus rule requires. The read payload
# shows ``prior_price`` and ``announced_discount_percent``; the markdown is
# written through ``ProductWriteSerializer``.
class ProductSerializer(
    HiddenReviewsMixin,
    TranslatableModelSerializer,
    serializers.ModelSerializer[Product],
    EffectivePriceFieldsSerializer,
):
    translations = TranslatedFieldsFieldExtend(shared_model=Product)
    # Read-only (every row carries it), declared with the column's own
    # range: a read-only ModelSerializer field drops its validators, and
    # with them the schema's minimum and maximum.
    stock = serializers.IntegerField(
        read_only=True,
        label=_STOCK_FIELD.verbose_name,
        min_value=_STOCK_RANGE[0],
        max_value=_STOCK_RANGE[1],
    )
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
    vat_value = MoneyField(max_digits=11, decimal_places=2, read_only=True)
    weight = MeasurementSerializerField(
        measurement=Weight, required=False, allow_null=True
    )
    attributes = ProductAttributeSerializer(
        source="product_attributes", many=True, read_only=True
    )
    # The group's translated name, in the request language. ``null``
    # without a group or without a name.
    variant_group_title = serializers.SerializerMethodField()
    image_paths = serializers.ListField(
        child=serializers.CharField(), read_only=True
    )
    # Reads the ``with_offer_kind()`` annotation and nothing else: a
    # product nested in a cart, order or favourite line comes from a
    # queryset without it, and resolving it per row would add a query to
    # every line of surfaces that show no offer badge. Those payloads
    # carry ``null``; the catalogue feed and detail annotate.
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
            "vat_percent",
            "vat_value",
            "final_price",
            "effective_price",
            "prior_price",
            "announced_discount_percent",
            "reduction_started_at",
            "main_image_path",
            "image_paths",
            "review_average",
            "review_count",
            "likes_count",
            "created_at",
            "updated_at",
            "uuid",
            "attributes",
            "offer_kind",
            "variant_group_title",
        )

        read_only_fields = (
            "id",
            "variant_group",
            "brand",
            "brand_name",
            "vat_percent",
            "vat_value",
            "final_price",
            "effective_price",
            "prior_price",
            "announced_discount_percent",
            "reduction_started_at",
            "main_image_path",
            "image_paths",
            "review_average",
            "review_count",
            "likes_count",
            "view_count",
            # Responses only (writes go through ProductWriteSerializer), so
            # the schema states what every row carries: a non-null model
            # field. ``stock`` is declared above to keep its range.
            "active",
            "low_stock_threshold",
            "created_at",
            "updated_at",
            "uuid",
            "attributes",
        )

    def get_variant_group_title(self, obj: Product) -> str | None:
        group = obj.variant_group
        if group is None:
            return None
        return group.safe_translation_getter("name") or None


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
        # Hidden reviews hide the bars too: all zeros, without the query.
        counts = (
            dict(
                obj.reviews.get_queryset()
                .approved()
                .order_by()
                .values_list("rate")
                .annotate(count=Count("pk"))
            )
            if self.reviews_visible
            else {}
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
                _("Price must be greater than zero."), code="price_not_positive"
            )
        if value.amount > 99999:
            raise serializers.ValidationError(
                _("Price cannot exceed 99,999."), code="price_too_large"
            )
        return value

    def validate_stock(self, value: int) -> int:
        if value < 0:
            raise serializers.ValidationError(
                _("Stock cannot be negative."), code="stock_negative"
            )
        return value

    def validate_discount_percent(self, value: int) -> int:
        if value < 0 or value > 100:
            raise serializers.ValidationError(
                _("Discount percent must be between 0 and 100."),
                code="discount_out_of_range",
            )
        return value

    def validate_slug(self, value: str) -> str:
        if not value:
            raise serializers.ValidationError(
                _("Slug is required."), code="slug_required"
            )

        queryset = Product.objects.filter(slug=value)
        if self.instance:
            queryset = queryset.exclude(pk=self.instance.pk)

        if queryset.exists():
            raise serializers.ValidationError(
                _("A post with this slug already exists."), code="slug_taken"
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
