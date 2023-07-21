from django.db import models
from django.utils.translation import gettext_lazy as _
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
        db_index=True,
        max_length=2,
    )
    alpha_3 = models.CharField(
        _("Country Code Alpha 3"), unique=True, db_index=True, max_length=3
    )
    iso_cc = models.PositiveSmallIntegerField(
        _("ISO Country Code"), blank=True, null=True, unique=True
    )
    phone_code = models.PositiveSmallIntegerField(
        _("Phone Code"), blank=True, null=True, unique=True
    )
    image_flag = models.ImageField(
        _("Image Flag"), blank=True, null=True, upload_to="uploads/country/"
    )
    translations = TranslatedFields(
        name=models.CharField(_("Name"), max_length=50, blank=True, null=True)
    )

    class Meta:
        verbose_name = _("Country")
        verbose_name_plural = _("Countries")

    def __unicode__(self):
        return self.safe_translation_getter("name", any_language=True)

    def __str__(self):
        return self.safe_translation_getter("name", any_language=True)

    def get_ordering_queryset(self):
        return Country.objects.all()
