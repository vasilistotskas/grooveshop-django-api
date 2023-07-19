from django.db import models
from django.utils.translation import gettext_lazy as _
from parler.models import TranslatableModel
from parler.models import TranslatedFields

from core.models import SortableModel
from core.models import TimeStampMixinModel
from core.models import UUIDModel


class Region(TranslatableModel, TimeStampMixinModel, SortableModel, UUIDModel):
    alpha = models.CharField(_("Alpha"), max_length=10, primary_key=True, unique=True)
    alpha_2 = models.ForeignKey(
        "country.Country", related_name="region_alpha_2", on_delete=models.CASCADE
    )
    translations = TranslatedFields(
        name=models.CharField(_("Name"), max_length=100, blank=True, null=True)
    )

    class Meta:
        verbose_name = _("Region")
        verbose_name_plural = _("Regions")
        ordering = ["sort_order"]

    def __unicode__(self):
        return self.safe_translation_getter("name", any_language=True)

    def __str__(self):
        return self.safe_translation_getter("name", any_language=True)

    def get_ordering_queryset(self):
        return Region.objects.all()
