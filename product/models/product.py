from __future__ import annotations

import os
import uuid
from decimal import Decimal

from django.conf import settings
from django.contrib.postgres.indexes import BTreeIndex
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import (
    Avg,
    Case,
    Count,
    DecimalField,
    F,
    FloatField,
    OuterRef,
    Subquery,
    Value,
    When,
)
from django.db.models.functions import Coalesce
from django.utils.safestring import SafeString, mark_safe
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from djmoney.models.fields import MoneyField
from djmoney.money import Money
from measurement.measures import Weight
from mptt.fields import TreeForeignKey
from parler.fields import TranslationsForeignKey
from parler.managers import TranslatableManager, TranslatableQuerySet
from parler.models import TranslatableModel, TranslatedFieldsModel
from simple_history.models import HistoricalRecords
from tinymce.models import HTMLField

from core.fields.measurement import MeasurementField
from core.models import (
    MetaDataModel,
    SoftDeleteModel,
    SoftDeleteQuerySet,
    TimeStampMixinModel,
    UUIDModel,
)
from core.units import WeightUnits
from core.utils.generators import SlugifyConfig, unique_slugify
from core.weight import zero_weight
from meili.models import IndexMixin
from product.enum.review import ReviewStatusEnum
from product.models.favourite import ProductFavourite
from product.models.image import ProductImage
from product.models.review import ProductReview
from seo.models import SeoModel
from tag.models.tagged_item import TaggedModel

DISCOUNT_PERCENT_MIN = Decimal("0.0")
DISCOUNT_PERCENT_MAX = Decimal("100.0")


class ProductQuerySet(TranslatableQuerySet, SoftDeleteQuerySet):
    def exclude_deleted(self):
        return self.exclude(is_deleted=True)

    def with_discount_value(self):
        return self.annotate(
            discount_value_amount=F("price")
            * F("discount_percent")
            / Value(100, output_field=DecimalField())
        )

    def with_vat_value(self):
        return self.annotate(
            vat_value_amount=Case(
                When(
                    vat__isnull=False,
                    then=F("price")
                    * F("vat__value")
                    / Value(100, output_field=DecimalField()),
                ),
                default=Value(0, output_field=DecimalField()),
            )
        )

    def with_final_price(self):
        queryset = self.with_discount_value().with_vat_value()
        return queryset.annotate(
            final_price_amount=F("price")
            + F("vat_value_amount")
            - F("discount_value_amount")
        )

    def with_price_save_percent(self):
        queryset = self.with_discount_value()
        return queryset.annotate(
            price_save_percent_field=Case(
                When(
                    price__gt=0,
                    then=F("discount_value_amount")
                    / F("price")
                    * Value(100, output_field=DecimalField()),
                ),
                default=Value(0, output_field=DecimalField()),
            )
        )

    def with_likes_count(self):
        from product.models.favourite import ProductFavourite

        likes_subquery = (
            ProductFavourite.objects.filter(product_id=OuterRef("pk"))
            .values("product_id")
            .annotate(count=Count("id"))
            .values("count")
        )

        return self.annotate(
            likes_count_field=Coalesce(Subquery(likes_subquery), Value(0))
        )

    def with_review_average(self):
        from product.models.review import ProductReview

        reviews_avg_subquery = (
            ProductReview.objects.filter(product_id=OuterRef("pk"))
            .values("product_id")
            .annotate(
                avg=Coalesce(Avg("rate"), Value(0, output_field=FloatField()))
            )
            .values("avg")
        )

        return self.annotate(
            review_average_field=Coalesce(
                Subquery(reviews_avg_subquery),
                Value(0, output_field=FloatField()),
            )
        )

    def with_approved_review_average(self):
        from product.enum.review import ReviewStatusEnum
        from product.models.review import ProductReview

        approved_reviews_avg_subquery = (
            ProductReview.objects.filter(
                product_id=OuterRef("pk"), status=ReviewStatusEnum.TRUE
            )
            .values("product_id")
            .annotate(
                avg=Coalesce(Avg("rate"), Value(0, output_field=FloatField()))
            )
            .values("avg")
        )

        return self.annotate(
            approved_review_average_field=Coalesce(
                Subquery(approved_reviews_avg_subquery),
                Value(0, output_field=FloatField()),
            )
        )

    def with_all_annotations(self):
        return (
            self.with_final_price()
            .with_price_save_percent()
            .with_likes_count()
            .with_review_average()
            .with_approved_review_average()
        )


class ProductManager(TranslatableManager):
    def get_queryset(self):
        return ProductQuerySet(self.model, using=self._db).exclude_deleted()

    def with_discount_value(self):
        return self.get_queryset().with_discount_value()

    def with_vat_value(self):
        return self.get_queryset().with_vat_value()

    def with_final_price(self):
        return self.get_queryset().with_final_price()

    def with_price_save_percent(self):
        return self.get_queryset().with_price_save_percent()

    def with_likes_count(self):
        return self.get_queryset().with_likes_count()

    def with_review_average(self):
        return self.get_queryset().with_review_average()

    def with_approved_review_average(self):
        return self.get_queryset().with_approved_review_average()

    def with_all_annotations(self):
        return self.get_queryset().with_all_annotations()


class Product(
    SoftDeleteModel,
    TranslatableModel,
    TimeStampMixinModel,
    SeoModel,
    UUIDModel,
    MetaDataModel,
    TaggedModel,
):
    id = models.BigAutoField(primary_key=True)
    product_code = models.CharField(
        _("Product Code"), unique=True, max_length=100, default=uuid.uuid4
    )
    category = TreeForeignKey(
        "product.ProductCategory",
        on_delete=models.SET_NULL,
        related_name="products",
        null=True,
        blank=True,
    )
    slug = models.SlugField(_("Slug"), max_length=255, unique=True)
    price = MoneyField(
        _("Price"),
        max_digits=11,
        decimal_places=2,
        default=0,
    )
    active = models.BooleanField(_("Active"), default=True)
    stock = models.PositiveIntegerField(_("Stock"), default=0)
    discount_percent = models.DecimalField(
        _("Discount Percent"),
        max_digits=11,
        decimal_places=2,
        default=Decimal("0.0"),
    )
    vat = models.ForeignKey(
        "vat.Vat",
        related_name="products",
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
    )
    view_count = models.PositiveBigIntegerField(_("View Count"), default=0)
    weight = MeasurementField(
        _("Weight"),
        measurement=Weight,
        unit_choices=WeightUnits.CHOICES,
        default=zero_weight,
    )
    changed_by = models.ForeignKey(
        "user.UserAccount",
        on_delete=models.SET_NULL,
        related_name="changed_products",
        null=True,
        blank=True,
    )
    history = HistoricalRecords()

    objects = ProductManager()

    class Meta(MetaDataModel.Meta, TypedModelMeta):
        verbose_name = _("Product")
        verbose_name_plural = _("Products")
        ordering = ["-created_at"]
        indexes = [
            *MetaDataModel.Meta.indexes,
            *TimeStampMixinModel.Meta.indexes,
            BTreeIndex(
                fields=["price", "stock"], name="product_price_stock_ix"
            ),
            BTreeIndex(fields=["product_code"], name="product_product_code_ix"),
            BTreeIndex(fields=["slug"], name="product_slug_ix"),
            BTreeIndex(fields=["price"], name="product_price_ix"),
            BTreeIndex(fields=["stock"], name="product_stock_ix"),
            BTreeIndex(fields=["discount_percent"], name="product_discount_ix"),
            BTreeIndex(fields=["view_count"], name="product_view_count_ix"),
            BTreeIndex(fields=["weight"], name="product_weight_ix"),
            BTreeIndex(fields=["active"], name="product_active_ix"),
            BTreeIndex(fields=["category"], name="product_category_ix"),
            BTreeIndex(
                fields=["active", "price"], name="product_active_price_ix"
            ),
        ]

    def __str__(self):
        return self.safe_translation_getter("name", any_language=True) or ""

    def __repr__(self):
        name = self.safe_translation_getter("name", any_language=True) or ""
        return f"<Product: {name} ({self.product_code})>"

    def save(self, *args, **kwargs):
        if not self.product_code:
            self.product_code = self.generate_unique_product_code()

        if not self.slug:
            config = SlugifyConfig(
                instance=self,
                title_field="name",
            )
            self.slug = unique_slugify(config)
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.discount_percent > 0 >= self.price.amount:
            raise ValidationError(
                {
                    "discount_percent": _(
                        "Discount percent cannot be greater than 0 if price is 0."
                    )
                }
            )

        if (
            not DISCOUNT_PERCENT_MIN
            <= self.discount_percent
            <= DISCOUNT_PERCENT_MAX
        ):
            raise ValidationError(
                {
                    "discount_percent": _(
                        "Discount percent must be between 0 and 100."
                    )
                }
            )

        if self.stock < 0:
            raise ValidationError({"stock": _("Stock cannot be negative.")})

    def generate_unique_product_code(self) -> uuid.UUID:
        while True:
            unique_code = uuid.uuid4()
            if not self.objects.filter(product_code=unique_code).exists():
                return unique_code

    def increment_stock(self, quantity: int) -> None:
        if quantity < 0:
            raise ValueError("Quantity to increment must be non-negative")
        Product.objects.filter(id=self.id).update(stock=F("stock") + quantity)
        self.refresh_from_db()

    def decrement_stock(self, quantity: int) -> None:
        if quantity < 0:
            raise ValueError("Invalid quantity to decrement")
        updated_rows = Product.objects.filter(
            id=self.id, stock__gte=quantity
        ).update(stock=F("stock") - quantity)
        if not updated_rows:
            raise ValueError("Not enough stock to decrement")
        self.refresh_from_db()

    @property
    def _history_user(self):
        return self.changed_by

    @_history_user.setter
    def _history_user(self, value):
        self.changed_by = value

    @property
    def discount_value(self) -> Money:
        value = (self.price.amount * self.discount_percent) / 100
        return Money(value, settings.DEFAULT_CURRENCY)

    @property
    def price_save_percent(self) -> Decimal:
        if self.price.amount > 0:
            return Decimal(
                (self.discount_value.amount / self.price.amount) * 100
            )
        return Decimal(0)

    @property
    def likes_count(self) -> int:
        return ProductFavourite.objects.filter(product=self).count()

    @property
    def review_average(self) -> float:
        average = ProductReview.objects.filter(product=self).aggregate(
            avg=Avg("rate")
        )["avg"]
        return float(average) if average is not None else 0.0

    @property
    def approved_review_average(self) -> float:
        average = ProductReview.objects.filter(
            product=self, status=ReviewStatusEnum.TRUE
        ).aggregate(avg=Avg("rate"))["avg"]
        return float(average) if average is not None else 0.0

    @property
    def review_count(self) -> int:
        return ProductReview.objects.filter(product=self).count()

    @property
    def approved_review_count(self) -> int:
        return ProductReview.objects.filter(
            product=self, status=ReviewStatusEnum.TRUE
        ).count()

    @property
    def vat_percent(self) -> Decimal:
        if self.vat:
            return self.vat.value
        return Decimal(0)

    @property
    def vat_value(self) -> Money:
        if self.vat:
            value = (self.price.amount * self.vat.value) / 100
            return Money(value, settings.DEFAULT_CURRENCY)
        return Money(0, settings.DEFAULT_CURRENCY)

    @property
    def final_price(self) -> Money:
        price_currency = self.price.currency
        vat_value = (
            Money(self.vat_value.amount, price_currency)
            if self.vat_value.currency != price_currency
            else self.vat_value
        )
        discount_value = (
            Money(self.discount_value.amount, price_currency)
            if self.discount_value.currency != price_currency
            else self.discount_value
        )
        return self.price + vat_value - discount_value

    @property
    def main_image_path(self) -> str:
        product_image = ProductImage.objects.filter(
            product_id=self.id, is_main=True
        ).first()
        if not product_image:
            return ""
        return f"media/uploads/products/{os.path.basename(product_image.image.name)}"

    @property
    def colored_stock(self) -> SafeString:
        if self.stock > 0:
            return mark_safe(
                '<span style="color: #1bff00;">{}</span>'.format(self.stock)
            )
        else:
            return mark_safe(
                '<span style="color: #ff0000;">{}</span>'.format(self.stock)
            )

    @property
    def absolute_url(self) -> str:
        return f"/products/{self.id}/{self.slug}"


class ProductTranslation(TranslatedFieldsModel, IndexMixin):
    master = TranslationsForeignKey(
        "product.Product",
        on_delete=models.CASCADE,
        related_name="translations",
        null=True,
    )
    name = models.CharField(_("Name"), max_length=255, blank=True, default="")
    description = HTMLField(_("Description"), blank=True, null=True)

    class Meta:
        app_label = "product"
        db_table = "product_product_translation"
        unique_together = ("language_code", "master")
        verbose_name = _("Product Translation")
        verbose_name_plural = _("Product Translations")

    class MeiliMeta:
        filterable_fields = (
            "name",
            "language_code",
            "likes_count",
            "final_price",
            "view_count",
            "category",
        )
        searchable_fields = ("id", "name", "description")
        displayed_fields = (
            "id",
            "name",
            "description",
            "language_code",
            "likes_count",
            "final_price",
            "view_count",
            "category",
        )
        sortable_fields = (
            "likes_count",
            "final_price",
            "view_count",
            "discount_percent",
        )
        ranking_rules = [
            "words",
            "typo",
            "proximity",
            "attribute",
            "sort",
            "likes_count:desc",
            "view_count:desc",
            "final_price:desc",
            "exactness",
        ]
        synonyms = {}
        typo_tolerance = {
            "enabled": True,
            "minWordSizeForTypos": {"oneTypo": 3, "twoTypos": 5},
            "disableOnWords": [],
            "disableOnAttributes": [],
        }
        faceting = {"maxValuesPerFacet": 100}
        pagination = {"maxTotalHits": 1000}

    @classmethod
    def get_additional_meili_fields(cls):
        return {
            "likes_count": lambda obj: obj.master.likes_count,
            "view_count": lambda obj: obj.master.view_count,
            "final_price": lambda obj: float(obj.master.final_price.amount),
        }

    def __str__(self):
        model = self._meta.verbose_name.title()
        return f"{model:s}: {self.name:s}"
