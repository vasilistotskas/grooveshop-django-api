from __future__ import annotations

import os
import uuid
from decimal import Decimal

from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVector
from django.contrib.postgres.search import SearchVectorField
from django.db import models
from django.db.models import Avg
from django.db.models import Count
from django.db.models import Subquery
from django.db.models.functions import Coalesce
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.safestring import SafeString
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from djmoney.models.fields import MoneyField
from djmoney.money import Money
from mptt.fields import TreeForeignKey
from parler.managers import TranslatableQuerySet
from parler.models import TranslatableModel
from parler.models import TranslatedFields
from tinymce.models import HTMLField

from core.models import ModelWithMetadata
from core.models import TimeStampMixinModel
from core.models import UUIDModel
from product.models.favourite import ProductFavourite
from product.models.image import ProductImage
from product.models.review import ProductReview
from seo.models import SeoModel


class ProductQuerySet(TranslatableQuerySet):
    def update_search_vector(self):
        search_vector = (
            SearchVector("translations__name", weight="A")
            + SearchVector("slug", weight="B")
            + SearchVector("translations__description", weight="C")
        )

        subquery = self.annotate(updated_vector=search_vector).values("updated_vector")[
            :1
        ]

        return self.update(search_vector=Subquery(subquery))

    def update_calculated_fields(self) -> ProductQuerySet:
        vat_subquery = models.Subquery(
            Product.objects.filter(vat__isnull=False).values("vat__value")[:1]
        )

        annotated_queryset = self.annotate(
            vat_value_annotation=models.ExpressionWrapper(
                (models.F("price") * Coalesce(vat_subquery, 0)) / 100,
                output_field=MoneyField(max_digits=19, decimal_places=4),
            ),
            discount_value_annotation=models.ExpressionWrapper(
                (models.F("price") * models.F("discount_percent")) / 100,
                output_field=MoneyField(max_digits=19, decimal_places=4),
            ),
            final_price_annotation=models.ExpressionWrapper(
                models.F("price")
                + models.F("vat_value_annotation")
                - models.F("discount_value_annotation"),
                output_field=MoneyField(max_digits=19, decimal_places=4),
            ),
            price_save_percent_annotation=models.ExpressionWrapper(
                (models.F("discount_value_annotation") / models.F("price")) * 100,
                output_field=MoneyField(max_digits=19, decimal_places=4),
            ),
        )

        return annotated_queryset.update(
            discount_value=models.F("discount_value_annotation"),
            final_price=models.F("final_price_annotation"),
            price_save_percent=models.F("price_save_percent_annotation"),
        )


class ProductManager(models.Manager):
    def get_queryset(self) -> ProductQuerySet:
        return ProductQuerySet(self.model, using=self._db)

    def update_search_vector(self):
        return self.get_queryset().update_search_vector()

    def update_calculated_fields(self) -> ProductQuerySet:
        return self.get_queryset().update_calculated_fields()


class Product(
    TranslatableModel, TimeStampMixinModel, SeoModel, UUIDModel, ModelWithMetadata
):
    id = models.BigAutoField(primary_key=True)
    product_code = models.CharField(
        _("Product Code"), unique=True, max_length=100, default=uuid.uuid4
    )
    category = TreeForeignKey(
        "product.ProductCategory",
        on_delete=models.SET_NULL,
        related_name="product_category",
        null=True,
        blank=True,
    )
    slug = models.SlugField(_("Slug"), max_length=255, unique=True)
    price = MoneyField(
        _("Price"),
        max_digits=19,
        decimal_places=4,
        default=0,
    )
    active = models.BooleanField(_("Active"), default=True)
    stock = models.PositiveIntegerField(_("Stock"), default=0)
    discount_percent = models.DecimalField(
        _("Discount Percent"), max_digits=11, decimal_places=2, default=0.0
    )
    vat = models.ForeignKey(
        "vat.Vat",
        related_name="product_vat",
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
    )
    hits = models.PositiveIntegerField(_("Hits"), default=0)
    weight = models.DecimalField(
        _("Weight (kg)"), max_digits=11, decimal_places=2, default=0.0
    )

    # final_price, discount_value, price_save_percent are calculated fields on save method
    final_price = MoneyField(
        _("Final Price"),
        max_digits=19,
        decimal_places=4,
        default=0,
        editable=False,
    )
    discount_value = MoneyField(
        _("Discount Value"),
        max_digits=19,
        decimal_places=4,
        default=0,
        editable=False,
    )
    price_save_percent = models.DecimalField(
        _("Price Save Percent"),
        max_digits=11,
        decimal_places=2,
        default=0.0,
        editable=False,
    )

    translations = TranslatedFields(
        name=models.CharField(
            _("Name"), max_length=255, blank=True, null=True, db_index=True
        ),
        description=HTMLField(_("Description"), blank=True, null=True, db_index=True),
    )
    search_vector = SearchVectorField(blank=True, null=True)

    objects = ProductManager()

    class Meta(ModelWithMetadata.Meta, TypedModelMeta):
        verbose_name = _("Product")
        verbose_name_plural = _("Products")
        ordering = ["-created_at"]
        indexes = [
            *ModelWithMetadata.Meta.indexes,
            GinIndex(fields=["search_vector"], name="product_search_vector_idx"),
        ]

    def __unicode__(self):
        return self.safe_translation_getter("name", any_language=True) or ""

    def __str__(self):
        return self.safe_translation_getter("name", any_language=True) or ""

    @property
    def likes_counter(self) -> int:
        favourites = ProductFavourite.objects.filter(product=self).aggregate(
            count=Count("id")
        )
        cnt: int = 0
        if favourites["count"] is not None:
            cnt = int(favourites["count"])
        return cnt

    @property
    def review_average(self) -> float:
        reviews = ProductReview.objects.filter(product=self, status="True").aggregate(
            average=Avg("rate")
        )
        avg: float = 0.0
        if reviews["average"] is not None:
            avg = float(reviews["average"])
        return avg

    @property
    def review_counter(self) -> int:
        reviews = ProductReview.objects.filter(product=self, status="True").aggregate(
            count=Count("id")
        )
        cnt: int = 0
        if reviews["count"] is not None:
            return int(reviews["count"])
        return cnt

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
    def image_tag(self) -> str:
        no_img_url = "/static/images/no_photo.jpg"
        no_img_markup = f'<img src="{no_img_url}" width="100" height="100" />'
        try:
            img = ProductImage.objects.get(product_id=self.id, is_main=True)
        except ProductImage.DoesNotExist:
            return no_img_markup

        if img.thumbnail:
            return mark_safe(
                '<img src="{}" width="100" height="100" />'.format(img.thumbnail.url)
            )
        else:
            return mark_safe(
                '<img src="{}" width="100" height="100" />'.format(img.image.url)
            )

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
