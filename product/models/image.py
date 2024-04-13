from __future__ import annotations

import os

from django.conf import settings
from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from parler.managers import TranslatableManager
from parler.models import TranslatableModel
from parler.models import TranslatedFields

from core.models import SortableModel
from core.models import TimeStampMixinModel
from core.models import UUIDModel
from helpers.image_resize import make_thumbnail


class ProductImageManager(TranslatableManager):
    def main_image(self, product):
        return self.get_queryset().filter(product=product, is_main=True).first()


class ProductImage(TranslatableModel, TimeStampMixinModel, SortableModel, UUIDModel):
    id = models.BigAutoField(primary_key=True)
    product = models.ForeignKey(
        "product.Product", related_name="product_images", on_delete=models.CASCADE
    )
    image = models.ImageField(_("Image"), upload_to="uploads/products/")
    thumbnail = models.ImageField(
        _("Thumbnail"), upload_to="uploads/products/thumbnails/", blank=True, null=True
    )
    is_main = models.BooleanField(_("Is Main"), default=False)
    translations = TranslatedFields(
        title=models.CharField(_("Title"), max_length=50, blank=True, null=True)
    )

    objects = ProductImageManager()

    class Meta(TypedModelMeta):
        verbose_name_plural = _("Product Images")
        verbose_name = _("Product Image")
        ordering = ["sort_order"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            *SortableModel.Meta.indexes,
            BTreeIndex(fields=["product", "is_main"]),
        ]

    def __unicode__(self):
        product_name = self.product.safe_translation_getter("name", any_language=True)
        main_status = "Main" if self.is_main else "Secondary"
        return f"{product_name} Image ({main_status})"

    def __str__(self):
        product_name = self.product.safe_translation_getter("name", any_language=True)
        main_status = "Main" if self.is_main else "Secondary"
        return f"{product_name} Image ({main_status})"

    def get_ordering_queryset(self):
        return self.product.product_images.all()

    def save(self, *args, **kwargs):
        if not self.thumbnail:
            self.thumbnail = self.create_thumbnail()
        super().save(*args, **kwargs)

    def clean(self):
        if self.is_main:
            ProductImage.objects.filter(product=self.product, is_main=True).update(
                is_main=False
            )
        super().clean()

    def create_thumbnail(self):
        try:
            return make_thumbnail(self.image, (100, 100))
        except Exception as e:
            print("Error while creating thumbnail: ", e)
            return None

    @property
    def main_image_absolute_url(self) -> str:
        image: str = ""
        if self.image and hasattr(self.image, "url"):
            return settings.APP_BASE_URL + self.image.url
        return image

    @property
    def main_image_filename(self) -> str:
        if self.image and hasattr(self.image, "name"):
            return os.path.basename(self.image.name)
        else:
            return ""
