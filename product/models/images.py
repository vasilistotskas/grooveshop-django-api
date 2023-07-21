from __future__ import annotations

import os

from django.conf import settings
from django.db import models
from django.db.models.fields.files import ImageFieldFile

from core.models import SortableModel
from core.models import TimeStampMixinModel
from core.models import UUIDModel
from helpers.image_resize import make_thumbnail


class ProductImages(TimeStampMixinModel, SortableModel, UUIDModel):
    id = models.BigAutoField(primary_key=True)
    title = models.CharField(max_length=50)
    product = models.ForeignKey(
        "product.Product", related_name="product_images", on_delete=models.CASCADE
    )
    image = models.ImageField(upload_to="uploads/products/")
    thumbnail = models.ImageField(
        upload_to="uploads/products/thumbnails/", blank=True, null=True
    )
    is_main = models.BooleanField(default=False)

    class Meta:
        verbose_name_plural = "Product Images"

    def __str__(self):
        return self.title

    def get_ordering_queryset(self):
        return self.product.product_images.all()

    def save(self, *args, **kwargs):
        image: ImageFieldFile = self.image
        if image:
            self.thumbnail = make_thumbnail(image, (100, 100))

        super().save(*args, **kwargs)

    @property
    def product_image_absolute_url(self) -> str:
        image: str = ""
        if self.image:
            return settings.APP_BASE_URL + self.image.url
        return image

    @property
    def product_image_filename(self) -> str:
        if self.image:
            return os.path.basename(self.image.name)
        else:
            return ""
