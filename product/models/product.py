from __future__ import annotations

import os
import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Avg
from django.db.models import Count
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.safestring import SafeString
from django.utils.translation import gettext_lazy as _
from mptt.fields import TreeForeignKey
from parler.models import TranslatableModel
from parler.models import TranslatedFields
from tinymce.models import HTMLField

from core.models import TimeStampMixinModel
from core.models import UUIDModel
from helpers.image_resize import make_thumbnail
from product.models.favourite import ProductFavourite
from product.models.images import ProductImages
from product.models.review import ProductReview
from seo.models import SeoModel


class Product(TranslatableModel, TimeStampMixinModel, SeoModel, UUIDModel):
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
    price = models.DecimalField(_("Price"), max_digits=11, decimal_places=2)
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
    final_price = models.DecimalField(
        _("Final Price"), max_digits=11, decimal_places=2, default=0.0, editable=False
    )
    discount_value = models.DecimalField(
        _("Discount Value"),
        max_digits=11,
        decimal_places=2,
        default=0.0,
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
        name=models.CharField(_("Name"), max_length=255, blank=True, null=True),
        description=HTMLField(_("Description"), blank=True, null=True),
    )

    class Meta:
        verbose_name = _("Product")
        verbose_name_plural = _("Products")
        ordering = ["-created_at"]

    def __unicode__(self):
        return self.safe_translation_getter("name", any_language=True) or ""

    def __str__(self):
        return self.safe_translation_getter("name", any_language=True) or ""

    def save(self, *args, **kwargs):
        vat_value = 0.0
        if self.vat:
            vat_value = (self.price * self.vat.value) / 100

        self.discount_value = (self.price * self.discount_percent) / 100
        self.final_price = (
            float(self.price) + float(vat_value) - float(self.discount_value)
        )
        self.price_save_percent = (
            (self.price - self.discount_value)
            * (self.price - self.price - self.discount_value)
        ) / self.price
        super().save(*args, **kwargs)

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
    def vat_value(self) -> Decimal | int:
        if self.vat:
            return (self.price * self.vat.value) / 100
        return 0

    @property
    def main_image_absolute_url(self) -> str:
        img = ProductImages.objects.get(product_id=self.id, is_main=True)
        image: str = ""
        if img.image and hasattr(img.image, "url"):
            return settings.APP_BASE_URL + img.image.url
        return image

    @property
    def main_image_filename(self) -> str:
        product_image = ProductImages.objects.get(product_id=self.id, is_main=True)
        return os.path.basename(product_image.image.name)

    @property
    def image_tag(self) -> str:
        img = ProductImages.objects.get(product_id=self.id, is_main=True)
        if img.thumbnail:
            return mark_safe('<img src="{}"/>'.format(img.thumbnail.url))
        else:
            if img.image:
                img.thumbnail = make_thumbnail(img.image, (100, 100))
                img.save()
                return mark_safe('<img src="{}"/>'.format(img.thumbnail.url))
            else:
                return ""

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
