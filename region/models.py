from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from parler.models import TranslatableModel, TranslatedFields

from core.models import SortableModel, TimeStampMixinModel, UUIDModel
from region.managers import RegionManager


class Region(TranslatableModel, TimeStampMixinModel, SortableModel, UUIDModel):
    alpha = models.CharField(
        _("Region Code"), max_length=10, primary_key=True, unique=True
    )
    country = models.ForeignKey(
        "country.Country", related_name="regions", on_delete=models.CASCADE
    )
    translations = TranslatedFields(
        name=models.CharField(_("Name"), max_length=100, blank=True, null=True)
    )

    objects: RegionManager = RegionManager()

    class Meta(TypedModelMeta):
        verbose_name = _("Region")
        verbose_name_plural = _("Regions")
        ordering = ["sort_order"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            *SortableModel.Meta.indexes,
            BTreeIndex(fields=["alpha"], name="region_alpha_ix"),
            BTreeIndex(fields=["country"], name="region_country_ix"),
        ]

    def __str__(self):
        country_name = self.country.safe_translation_getter(
            "name", any_language=True
        )
        region_name = self.safe_translation_getter("name", any_language=True)
        return f"{region_name}, {country_name}"

    def get_ordering_queryset(self):
        return Region.objects.all()
