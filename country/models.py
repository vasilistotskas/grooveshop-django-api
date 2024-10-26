import os
from typing import override

from django.core.validators import RegexValidator
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from parler.models import TranslatableModel
from parler.models import TranslatedFields

from core.models import SortableModel
from core.models import TimeStampMixinModel
from core.models import UUIDModel


class Country(TranslatableModel, TimeStampMixinModel, SortableModel, UUIDModel):
    alpha_2 = models.CharField(
        _("Country Code Alpha 2"),
        primary_key=True,
        unique=True,
        max_length=2,
        validators=[
            RegexValidator(
                regex="^[A-Z]{2}$",
                message=_("Enter a valid 2-letter country code."),
            )
        ],
    )
    alpha_3 = models.CharField(
        _("Country Code Alpha 3"),
        unique=True,
        max_length=3,
        validators=[
            RegexValidator(
                regex="^[A-Z]{3}$",
                message=_("Enter a valid 3-letter country code."),
            )
        ],
    )
    iso_cc = models.PositiveSmallIntegerField(_("ISO Country Code"), blank=True, null=True, unique=True)
    phone_code = models.PositiveSmallIntegerField(_("Phone Code"), blank=True, null=True, unique=True)
    image_flag = models.ImageField(_("Image Flag"), blank=True, null=True, upload_to="uploads/country/")
    translations = TranslatedFields(name=models.CharField(_("Name"), max_length=100, blank=True, null=True))

    class Meta(TypedModelMeta):
        ordering = ["sort_order"]
        verbose_name = _("Country")
        verbose_name_plural = _("Countries")
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            *SortableModel.Meta.indexes,
            models.Index(fields=["alpha_2"]),
            models.Index(fields=["alpha_3"]),
        ]

    def __str__(self):
        return self.safe_translation_getter("name", any_language=True) or ""

    @override
    def save(self, *args, **kwargs):
        self.alpha_2 = self.alpha_2.upper()
        self.alpha_3 = self.alpha_3.upper()
        super().save(*args, **kwargs)

    @override
    def get_ordering_queryset(self):
        return Country.objects.all()

    @property
    def main_image_path(self) -> str:
        if self.image_flag and hasattr(self.image_flag, "name"):
            return f"media/uploads/country/{os.path.basename(self.image_flag.name)}"
        return ""
