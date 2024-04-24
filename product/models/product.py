from __future__ import annotations

import os
import uuid
from decimal import Decimal

from django.conf import settings
from django.contrib.postgres.indexes import BTreeIndex
from django.contrib.postgres.search import SearchVectorField
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Avg
from django.db.models import F
from django.db.models.functions import Coalesce
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
from parler.models import TranslatedFields
from tinymce.models import HTMLField

from core.fields.measurement import MeasurementField
from core.models import ModelWithMetadata
from core.models import SoftDeleteModel
from core.models import SoftDeleteQuerySet
from core.models import TimeStampMixinModel
from core.models import UUIDModel
from core.units import WeightUnits
from core.utils.generators import SlugifyConfig
from core.utils.generators import unique_slugify
from core.weight import zero_weight
from product.enum.review import ReviewStatusEnum
from product.models.favourite import ProductFavourite
from product.models.image import ProductImage
from product.models.review import ProductReview
from seo.models import SeoModel


class ProductQuerySet(TranslatableQuerySet, SoftDeleteQuerySet):
    def update_calculated_fields(self) -> ProductQuerySet:
        vat_subquery = models.Subquery(
            Product.objects.filter(vat__isnull=False).values("vat__value")[:1]
        )

        annotated_queryset = self.annotate(
            vat_value_annotation=models.ExpressionWrapper(
                (models.F("price") * Coalesce(vat_subquery, 0)) / 100,
                output_field=MoneyField(max_digits=11, decimal_places=2),
            ),
            discount_value_annotation=models.ExpressionWrapper(
                (models.F("price") * models.F("discount_percent")) / 100,
                output_field=MoneyField(max_digits=11, decimal_places=2),
            ),
            final_price_annotation=models.ExpressionWrapper(
                models.F("price")
                + models.F("vat_value_annotation")
                - models.F("discount_value_annotation"),
                output_field=MoneyField(max_digits=11, decimal_places=2),
            ),
            price_save_percent_annotation=models.ExpressionWrapper(
                (models.F("discount_value_annotation") / models.F("price")) * 100,
                output_field=MoneyField(max_digits=11, decimal_places=2),
            ),
        )

        return annotated_queryset.update(
            discount_value=models.F("discount_value_annotation"),
            final_price=models.F("final_price_annotation"),
            price_save_percent=models.F("price_save_percent_annotation"),
        )


class ProductManager(TranslatableManager):
    def get_queryset(self) -> ProductQuerySet:
        return ProductQuerySet(self.model, using=self._db).exclude(is_deleted=True)

    def update_calculated_fields(self) -> ProductQuerySet:
        return self.get_queryset().update_calculated_fields()


class Product(
    SoftDeleteModel,
    TranslatableModel,
    TimeStampMixinModel,
    SeoModel,
    UUIDModel,
    ModelWithMetadata,
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

    # final_price, discount_value, price_save_percent are calculated fields on save method
    final_price = MoneyField(
        _("Final Price"),
        max_digits=11,
        decimal_places=2,
        default=0,
        editable=False,
    )
    discount_value = MoneyField(
        _("Discount Value"),
        max_digits=11,
        decimal_places=2,
        default=0,
        editable=False,
    )
    price_save_percent = models.DecimalField(
        _("Price Save Percent"),
        max_digits=11,
        decimal_places=2,
        default=Decimal(0.0),
        editable=False,
    )

    translations = TranslatedFields(
        name=models.CharField(_("Name"), max_length=255, blank=True, null=True),
        description=HTMLField(_("Description"), blank=True, null=True),
        search_document=models.TextField(_("Search Document"), blank=True, default=""),
        search_vector=SearchVectorField(_("Search Vector"), blank=True, null=True),
        search_document_dirty=models.BooleanField(
            _("Search Document Dirty"), default=False
        ),
        search_vector_dirty=models.BooleanField(
            _("Search Vector Dirty"), default=False
        ),
    )

    objects = ProductManager()

    class Meta(ModelWithMetadata.Meta, TypedModelMeta):
        verbose_name = _("Product")
        verbose_name_plural = _("Products")
        ordering = ["-created_at"]
        indexes = [
            *ModelWithMetadata.Meta.indexes,
            *TimeStampMixinModel.Meta.indexes,
            models.Index(fields=["product_code"], name="product_product_code_idx"),
            models.Index(fields=["slug"], name="product_slug_idx"),
            models.Index(fields=["price", "stock"], name="product_price_stock_idx"),
            BTreeIndex(fields=["price"]),
            BTreeIndex(fields=["stock"]),
            BTreeIndex(fields=["discount_percent"]),
            BTreeIndex(fields=["view_count"]),
            BTreeIndex(fields=["weight"]),
            BTreeIndex(fields=["final_price"]),
            BTreeIndex(fields=["discount_value"]),
            BTreeIndex(fields=["price_save_percent"]),
        ]

    def __unicode__(self):
        return self.safe_translation_getter("name", any_language=True) or ""

    def __str__(self):
        return self.safe_translation_getter("name", any_language=True) or ""

    def __repr__(self):
        return f"<Product {self.name} ({self.product_code})>"

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

    def save_translation(self, *args, **kwargs):
        self.search_vector_dirty = True
        self.search_document_dirty = True
        super().save_translation(*args, **kwargs)

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

        if not 0.0 <= self.discount_percent <= 100.0:
            raise ValidationError(
                {"discount_percent": _("Discount percent must be between 0 and 100.")}
            )

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
        updated_rows = Product.objects.filter(id=self.id, stock__gte=quantity).update(
            stock=F("stock") - quantity
        )
        if not updated_rows:
            raise ValueError("Not enough stock to decrement")
        self.refresh_from_db()

    @property
    def likes_count(self) -> int:
        return ProductFavourite.objects.filter(product=self).count()

    @property
    def review_average(self) -> float:
        average = ProductReview.objects.filter(
            product=self, status=ReviewStatusEnum.TRUE
        ).aggregate(avg=Avg("rate"))["avg"]
        return float(average) if average is not None else 0.0

    @property
    def review_count(self) -> int:
        return ProductReview.objects.filter(
            product=self, status=ReviewStatusEnum.TRUE
        ).count()

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
    def main_image_absolute_url(self) -> str:
        img = ProductImage.objects.get(product_id=self.id, is_main=True)
        if not img:
            return ""
        image: str = ""
        if img.image and hasattr(img.image, "url"):
            return settings.APP_BASE_URL + img.image.url
        return image

    @property
    def main_image_filename(self) -> str:
        product_image = ProductImage.objects.get(product_id=self.id, is_main=True)
        if not product_image:
            return ""
        return os.path.basename(product_image.image.name)

    @property
    def image_tag(self):
        no_img_url = static("images/no_photo.jpg")
        no_img_markup = mark_safe(
            f'<img src="{no_img_url}" width="100" height="100" />'
        )
        try:
            img = ProductImage.objects.get(product_id=self.id, is_main=True)
        except ProductImage.DoesNotExist:
            return no_img_markup

        if img.thumbnail:
            return mark_safe(
                '<img src="{}" width="100" height="100" />'.format(img.thumbnail.url)
            )
        elif img.image:
            return mark_safe(
                '<img src="{}" width="100" height="100" />'.format(img.image.url)
            )
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
        return f"/{self.id}/{self.slug}"
