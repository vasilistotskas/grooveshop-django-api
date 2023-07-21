import os

from django.conf import settings
from django.db import models
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from parler.models import TranslatableModel
from parler.models import TranslatedFields

from core.models import SortableModel
from core.models import TimeStampMixinModel
from core.models import UUIDModel
from helpers.image_resize import make_thumbnail


class Slider(TranslatableModel, TimeStampMixinModel, UUIDModel):
    id = models.BigAutoField(primary_key=True)
    image = models.ImageField(
        _("Image"), upload_to="uploads/sliders/", blank=True, null=True
    )
    thumbnail = models.ImageField(
        _("Thumbnail"), upload_to="uploads/sliders/thumbnails/", blank=True, null=True
    )
    video = models.FileField(
        _("Video"), upload_to="uploads/sliders/videos/", null=True, blank=True
    )
    translations = TranslatedFields(
        name=models.CharField(_("Name"), max_length=50, blank=True, null=True),
        url=models.CharField(_("Url"), max_length=255, blank=True, null=True),
        title=models.CharField(_("Title"), max_length=40, blank=True, null=True),
        description=models.CharField(
            _("Description"), max_length=255, blank=True, null=True
        ),
    )

    class Meta:
        verbose_name = _("Slider")
        verbose_name_plural = _("Sliders")
        ordering = ["-created_at"]

    def __unicode__(self):
        return self.safe_translation_getter("name", any_language=True)

    def __str__(self):
        return self.safe_translation_getter("name", any_language=True)

    def save(self, *args, **kwargs):
        if self.image:
            self.thumbnail = make_thumbnail(self.image, (200, 200))

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

    @property
    def image_tag(self):
        thumbnail = self.thumbnail
        if thumbnail:
            return mark_safe('<img src="{}"/>'.format(thumbnail.url))
        return ""


class Slide(TranslatableModel, TimeStampMixinModel, SortableModel, UUIDModel):
    id = models.BigAutoField(primary_key=True)
    slider = models.ForeignKey(
        "slider.Slider", related_name="slide_slider", on_delete=models.CASCADE
    )
    discount = models.DecimalField(max_digits=11, decimal_places=2, default=0.0)
    show_button = models.BooleanField(blank=False, null=False, default=True)
    date_start = models.DateTimeField(auto_now_add=False)
    date_end = models.DateTimeField(auto_now_add=False)
    image = models.ImageField(upload_to="uploads/slides/", blank=True, null=True)
    thumbnail = models.ImageField(
        upload_to="uploads/slides/thumbnails/", blank=True, null=True
    )
    translations = TranslatedFields(
        name=models.CharField(_("Name"), max_length=50, blank=True, null=True),
        url=models.CharField(_("Url"), max_length=255, blank=True, null=True),
        title=models.CharField(_("Title"), max_length=40, blank=True, null=True),
        subtitle=models.CharField(_("Subtitle"), max_length=40, blank=True, null=True),
        description=models.CharField(
            _("Description"), max_length=255, blank=True, null=True
        ),
        button_label=models.CharField(
            _("Button Label"), max_length=25, blank=True, null=True
        ),
    )

    class Meta:
        verbose_name = _("Slide")
        verbose_name_plural = _("Slides")
        ordering = ["sort_order"]

    def __unicode__(self):
        return self.safe_translation_getter("name", any_language=True)

    def __str__(self):
        return self.safe_translation_getter("name", any_language=True)

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
            return settings.APP_BASE_URL + self.image.url
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
