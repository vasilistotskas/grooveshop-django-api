from django.db import models

from core.models import TimeStampMixinModel
from core.models import UUIDModel


class Vat(TimeStampMixinModel, UUIDModel):
    id = models.AutoField(primary_key=True)
    value = models.DecimalField(max_digits=11, decimal_places=1)

    class Meta:
        verbose_name_plural = "Value Added Tax"
        ordering = ["-updated_at"]

    def __str__(self):
        return "%s" % self.value

    @staticmethod
    def get_highest_vat_value() -> float:
        return Vat.objects.all().order_by("-value").first()
