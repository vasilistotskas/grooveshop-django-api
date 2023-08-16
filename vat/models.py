from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta

from core.models import TimeStampMixinModel
from core.models import UUIDModel


class Vat(TimeStampMixinModel, UUIDModel):
    id = models.BigAutoField(primary_key=True)
    value = models.DecimalField(_("Value"), max_digits=11, decimal_places=1)

    class Meta(TypedModelMeta):
        verbose_name = _("Vat")
        verbose_name_plural = _("Vats")
        ordering = ["-created_at"]

    def __str__(self):
        return "%s" % self.value

    @staticmethod
    def get_highest_vat_value() -> float:
        return Vat.objects.all().order_by("-value").first().value
