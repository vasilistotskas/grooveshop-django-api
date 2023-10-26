from __future__ import annotations

import os

from django.conf import settings
from django.db import models
from django.db.models.fields.files import ImageFieldFile
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from parler.models import TranslatableModel
from parler.models import TranslatedFields

from core.models import SortableModel
from core.models import TimeStampMixinModel
from core.models import UUIDModel
from helpers.image_resize import make_thumbnail


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

    class Meta(TypedModelMeta):
        verbose_name_plural = _("Product Images")
        verbose_name = _("Product Image")
        ordering = ["sort_order"]

    def __unicode__(self):
        return self.safe_translation_getter("title", any_language=True) or ""

    def __str__(self):
        return self.safe_translation_getter("title", any_language=True) or ""

    def get_ordering_queryset(self):
        return self.product.product_images.all()

    def save(self, *args, **kwargs):
        image: ImageFieldFile = self.image
        try:
            self.thumbnail = make_thumbnail(image, (100, 100))
        except Exception as e:
            print("Error while creating thumbnail: ", e)
            self.thumbnail = image

        super().save(*args, **kwargs)

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
