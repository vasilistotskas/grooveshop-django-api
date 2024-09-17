from decimal import Decimal
from typing import override

from django.contrib.postgres.indexes import BTreeIndex
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta

from core.models import TimeStampMixinModel
from core.models import UUIDModel


class Vat(TimeStampMixinModel, UUIDModel):
    id = models.BigAutoField(primary_key=True)
    value = models.DecimalField(_("Value"), max_digits=11, decimal_places=1, default=Decimal(0.0))

    class Meta(TypedModelMeta):
        verbose_name = _("Vat")
        verbose_name_plural = _("Vats")
        ordering = ["-created_at"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            BTreeIndex(fields=["value"]),
        ]

    def __unicode__(self):
        return f"{self.value}% VAT"

    def __str__(self):
        return f"{self.value}% VAT"

    @override
    def clean(self):
        if not 0 <= self.value <= 100:
            raise ValidationError(_("VAT value must be between 0 and 100."))

    @staticmethod
    def get_highest_vat_value() -> float:
        highest_vat = Vat.objects.all().order_by("-value").first()
        return highest_vat.value if highest_vat else 0.0
