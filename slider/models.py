import os

from django.contrib.postgres.indexes import BTreeIndex
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from djmoney.models.fields import MoneyField
from parler.models import TranslatableModel, TranslatedFields

from core.fields.image import ImageAndSvgField
from core.models import SortableModel, TimeStampMixinModel, UUIDModel


class Slider(TranslatableModel, TimeStampMixinModel, UUIDModel):
    id = models.BigAutoField(primary_key=True)
    image = ImageAndSvgField(
        _("Image"), upload_to="uploads/sliders/", blank=True, null=True
    )
    video = models.FileField(
        _("Video"), upload_to="uploads/sliders/videos/", null=True, blank=True
    )
    translations = TranslatedFields(
        name=models.CharField(_("Name"), max_length=50, blank=True, null=True),
        url=models.CharField(_("Url"), max_length=255, blank=True, null=True),
        title=models.CharField(
            _("Title"), max_length=40, blank=True, null=True
        ),
        description=models.CharField(
            _("Description"), max_length=255, blank=True, null=True
        ),
    )

    class Meta(TypedModelMeta):
        verbose_name = _("Slider")
        verbose_name_plural = _("Sliders")
        ordering = ["-created_at"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
        ]

    def __str__(self):
        return self.safe_translation_getter("name", any_language=True) or ""

    @property
    def main_image_path(self) -> str:
        if self.image and hasattr(self.image, "name"):
            return f"media/uploads/sliders/{os.path.basename(self.image.name)}"
        return ""


class Slide(TranslatableModel, TimeStampMixinModel, SortableModel, UUIDModel):
    id = models.BigAutoField(primary_key=True)
    slider = models.ForeignKey(
        "slider.Slider", related_name="slides", on_delete=models.CASCADE
    )
    discount = MoneyField(
        _("Discount"),
        max_digits=11,
        decimal_places=2,
        default=0,
    )
    show_button = models.BooleanField(
        _("Show Button"), blank=False, null=False, default=False
    )
    date_start = models.DateTimeField(_("Date Start"), auto_now_add=False)
    date_end = models.DateTimeField(_("Date End"), auto_now_add=False)
    image = ImageAndSvgField(
        _("Image"), upload_to="uploads/slides/", blank=True, null=True
    )
    translations = TranslatedFields(
        name=models.CharField(_("Name"), max_length=50, blank=True, null=True),
        url=models.URLField(_("Url"), max_length=255, blank=True, null=True),
        title=models.CharField(
            _("Title"), max_length=40, blank=True, null=True
        ),
        subtitle=models.CharField(
            _("Subtitle"), max_length=40, blank=True, null=True
        ),
        description=models.CharField(
            _("Description"), max_length=255, blank=True, null=True
        ),
        button_label=models.CharField(
            _("Button Label"), max_length=25, blank=True, null=True
        ),
    )

    class Meta(TypedModelMeta):
        verbose_name = _("Slide")
        verbose_name_plural = _("Slides")
        ordering = ["sort_order"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            *SortableModel.Meta.indexes,
            BTreeIndex(
                fields=["date_start"], name="slider_slide_date_start_ix"
            ),
            BTreeIndex(fields=["date_end"], name="slider_slide_date_end_ix"),
        ]

    def __str__(self):
        return f"{self.safe_translation_getter('title', any_language=True)} in {self.slider}"

    def get_ordering_queryset(self):
        return Slide.objects.all()

    def clean(self):
        if (
            self.date_start
            and self.date_end
            and self.date_start > self.date_end
        ):
            raise ValidationError(_("'Date Start' must be before 'Date End'."))
        super().clean()

    @property
    def main_image_path(self) -> str:
        if self.image and hasattr(self.image, "name"):
            return f"media/uploads/slides/{os.path.basename(self.image.name)}"
        return ""
