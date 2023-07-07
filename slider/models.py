import os

from django.conf import settings
from django.db import models
from django.utils.safestring import mark_safe

from core.models import SortableModel
from core.models import TimeStampMixinModel
from core.models import UUIDModel
from helpers.image_resize import make_thumbnail


class Slider(TimeStampMixinModel, UUIDModel):
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=40, unique=True)
    url = models.CharField(max_length=255, blank=True, null=True)
    title = models.CharField(max_length=40, blank=True, null=True)
    description = models.CharField(max_length=255, blank=True, null=True)
    image = models.ImageField(upload_to="uploads/sliders/", blank=True, null=True)
    thumbnail = models.ImageField(
        upload_to="uploads/sliders/thumbnails/", blank=True, null=True
    )
    video = models.FileField(upload_to="uploads/sliders/videos/", null=True, blank=True)

    class Meta:
        verbose_name_plural = "Sliders"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if self.image:
            self.thumbnail = make_thumbnail(self.image, (200, 200))

        super().save(*args, **kwargs)

    @property
    def main_image_absolute_url(self) -> str:
        image: str = ""
        if self.image and hasattr(self.image, "url"):
            return settings.BACKEND_BASE_URL + self.image.url
        return image

    @property
    def main_image_filename(self) -> str:
        if self.image and hasattr(self.image, "name"):
            return os.path.basename(self.image.name)
        else:
            return ""

    @property
    def image_tag(self):
        thumbnail = self.thumbnail
        if thumbnail:
            return mark_safe('<img src="{}"/>'.format(thumbnail.url))
        return ""


class Slide(TimeStampMixinModel, SortableModel, UUIDModel):
    id = models.AutoField(primary_key=True)
    slider = models.ForeignKey(
        "slider.Slider", related_name="slide_slider", on_delete=models.CASCADE
    )
    url = models.CharField(max_length=255, blank=True, null=True)
    title = models.CharField(max_length=40, blank=True, null=True)
    subtitle = models.CharField(max_length=40, blank=True, null=True)
    description = models.CharField(max_length=255, blank=True, null=True)
    discount = models.DecimalField(max_digits=11, decimal_places=2, default=0.0)
    button_label = models.CharField(max_length=10, blank=True, null=True)
    show_button = models.BooleanField(blank=False, null=False, default=True)
    date_start = models.DateTimeField(auto_now_add=False)
    date_end = models.DateTimeField(auto_now_add=False)
    order_position = models.IntegerField()
    image = models.ImageField(upload_to="uploads/slides/", blank=True, null=True)
    thumbnail = models.ImageField(
        upload_to="uploads/slides/thumbnails/", blank=True, null=True
    )

    class Meta:
        verbose_name_plural = "Slides"
        ordering = ("order_position",)

    def __str__(self):
        return "%s" % self.id

    def get_ordering_queryset(self):
        return Slide.objects.all()

    def save(self, *args, **kwargs):
        if self.image:
            self.thumbnail = make_thumbnail(self.image, (200, 200))

        super().save(*args, **kwargs)

    @property
    def main_image_absolute_url(self) -> str:
        image: str = ""
        if self.image and hasattr(self.image, "url"):
            return settings.BACKEND_BASE_URL + self.image.url
        return image

    @property
    def main_image_filename(self) -> str:
        if self.image and hasattr(self.image, "name"):
            return os.path.basename(self.image.name)
        else:
            return ""

    @property
    def image_tag(self):
        thumbnail = self.thumbnail
        if thumbnail:
            return mark_safe('<img src="{}"/>'.format(thumbnail.url))
        return ""
