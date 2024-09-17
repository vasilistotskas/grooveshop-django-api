from __future__ import annotations

import os
import uuid
from decimal import Decimal
from typing import override

from django.conf import settings
from django.contrib.postgres.indexes import BTreeIndex
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Avg
from django.db.models import F
from django.templatetags.static import static
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.safestring import SafeString
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from djmoney.models.fields import MoneyField
from djmoney.money import Money
from measurement.measures import Weight
from mptt.fields import TreeForeignKey
from parler.managers import TranslatableManager
from parler.managers import TranslatableQuerySet
from parler.models import TranslatableModel
from parler.models import TranslatedFieldsModel
from simple_history.models import HistoricalRecords
from tinymce.models import HTMLField

from core.fields.measurement import MeasurementField
from core.models import MetaDataModel
from core.models import SoftDeleteModel
from core.models import SoftDeleteQuerySet
from core.models import TimeStampMixinModel
from core.models import UUIDModel
from core.units import WeightUnits
from core.utils.generators import SlugifyConfig
from core.utils.generators import unique_slugify
from core.weight import zero_weight
from meili.models import IndexMixin
from product.enum.review import ReviewStatusEnum
from product.models.favourite import ProductFavourite
from product.models.image import ProductImage
from product.models.review import ProductReview
from seo.models import SeoModel
from tag.models.tagged_item import TaggedModel


class ProductQuerySet(TranslatableQuerySet, SoftDeleteQuerySet):
    def exclude_deleted(self):
        return self.exclude(is_deleted=True)


class ProductManager(TranslatableManager):
    @override
    def get_queryset(self):
        return ProductQuerySet(self.model, using=self._db).exclude_deleted()


class Product(SoftDeleteModel, TranslatableModel, TimeStampMixinModel, SeoModel, UUIDModel, MetaDataModel, TaggedModel):
    id = models.BigAutoField(primary_key=True)
    product_code = models.CharField(_("Product Code"), unique=True, max_length=100, default=uuid.uuid4)
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
        default=Decimal(0.0),
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
            models.Index(fields=["product_code"], name="product_product_code_idx"),
            models.Index(fields=["slug"], name="product_slug_idx"),
            models.Index(fields=["price", "stock"], name="product_price_stock_idx"),
            BTreeIndex(fields=["price"]),
            BTreeIndex(fields=["stock"]),
            BTreeIndex(fields=["discount_percent"]),
            BTreeIndex(fields=["view_count"]),
            BTreeIndex(fields=["weight"]),
        ]

    def __unicode__(self):
        return self.safe_translation_getter("name", any_language=True) or ""

    def __str__(self):
        return self.safe_translation_getter("name", any_language=True) or ""

    def __repr__(self):
        return f"<Product {self.name} ({self.product_code})>"

    @override
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

    @override
    def clean(self):
        super().clean()
        if self.discount_percent > 0 >= self.price.amount:
            raise ValidationError({"discount_percent": _("Discount percent cannot be greater than 0 if price is 0.")})

        if not 0.0 <= self.discount_percent <= 100.0:
            raise ValidationError({"discount_percent": _("Discount percent must be between 0 and 100.")})

        if self.stock < 0:
            raise ValidationError({"stock": _("Stock cannot be negative.")})

    def generate_unique_product_code(self) -> uuid.UUID:
        while True:
            unique_code = uuid.uuid4()
            if not Product.objects.filter(product_code=unique_code).exists():
                return unique_code

    def increment_stock(self, quantity: int):
        if quantity < 0:
            raise ValueError("Quantity to increment must be non-negative")
        Product.objects.filter(id=self.id).update(stock=F("stock") + quantity)
        self.refresh_from_db()

    def decrement_stock(self, quantity: int):
        if quantity < 0:
            raise ValueError("Invalid quantity to decrement")
        updated_rows = Product.objects.filter(id=self.id, stock__gte=quantity).update(stock=F("stock") - quantity)
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
            return (self.discount_value.amount / self.price.amount) * 100
        return Decimal(0)

    @property
    def likes_count(self) -> int:
        return ProductFavourite.objects.filter(product=self).count()

    @property
    def review_average(self) -> float:
        average = ProductReview.objects.filter(product=self).aggregate(avg=Avg("rate"))["avg"]
        return float(average) if average is not None else 0.0

    @property
    def approved_review_average(self) -> float:
        average = ProductReview.objects.filter(product=self, status=ReviewStatusEnum.TRUE).aggregate(avg=Avg("rate"))[
            "avg"
        ]
        return float(average) if average is not None else 0.0

    @property
    def review_count(self) -> int:
        return ProductReview.objects.filter(product=self).count()

    @property
    def approved_review_count(self) -> int:
        return ProductReview.objects.filter(product=self, status=ReviewStatusEnum.TRUE).count()

    @property
    def vat_percent(self) -> Decimal | int:
        if self.vat:
            return self.vat.value
        return 0

    @property
    def vat_value(self) -> Money:
        if self.vat:
            value = (self.price.amount * self.vat.value) / 100
            return Money(value, settings.DEFAULT_CURRENCY)
        return Money(0, settings.DEFAULT_CURRENCY)

    @property
    def final_price(self) -> Money:
        return self.price + self.vat_value - self.discount_value

    @property
    def main_image_path(self) -> str:
        product_image = ProductImage.objects.filter(product_id=self.id, is_main=True).first()
        if not product_image:
            return ""
        return f"media/uploads/products/{os.path.basename(product_image.image.name)}"

    @property
    def image_tag(self):
        no_img_url = static("images/no_photo.jpg")
        no_img_markup = mark_safe(f'<img src="{no_img_url}" width="100" height="100" />')
        try:
            img = ProductImage.objects.get(product_id=self.id, is_main=True)
        except ProductImage.DoesNotExist:
            return no_img_markup

        if img.thumbnail:
            return mark_safe('<img src="{}" width="100" height="100" />'.format(img.thumbnail.url))
        elif img.image:
            return mark_safe('<img src="{}" width="100" height="100" />'.format(img.image.url))
        else:
            return no_img_markup

    @property
    def colored_stock(self) -> SafeString | SafeString:
        if self.stock > 0:
            return format_html(
                '<span style="color: #1bff00;">{}</span>',
                self.stock,
            )
        else:
            return format_html(
                '<span style="color: #ff0000;">{}</span>',
                self.stock,
            )

    @property
    def absolute_url(self) -> str:
        return f"/products/{self.id}/{self.slug}"


class ProductTranslation(TranslatedFieldsModel, IndexMixin):
    master = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="translations", null=True)
    name = models.CharField(_("Name"), max_length=255, blank=True, null=True)
    description = HTMLField(_("Description"), blank=True, null=True)

    class Meta:
        app_label = "product"
        db_table = "product_product_translation"
        unique_together = ("language_code", "master")
        verbose_name = _("Product Translation")
        verbose_name_plural = _("Product Translations")

    class MeiliMeta:
        filterable_fields = ("name", "language_code", "likes_count", "final_price", "view_count", "category")
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
        sortable_fields = ("likes_count", "final_price", "view_count", "discount_percent")
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
    @override
    def get_additional_meili_fields(cls):
        return {
            "likes_count": lambda obj: obj.master.likes_count,
            "view_count": lambda obj: obj.master.view_count,
            "final_price": lambda obj: float(obj.master.final_price.amount),
        }

    def __str__(self):
        model = self._meta.verbose_name.title()
        return f"{model:s}: {self.name:s}"
