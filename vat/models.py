from decimal import Decimal

from django.contrib.postgres.indexes import BTreeIndex
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import CheckConstraint, Q
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta

from core.models import TimeStampMixinModel, UUIDModel

# Define constants at the module level
MIN_VAT_VALUE = Decimal("0.0")
MAX_VAT_VALUE = Decimal("100.0")


class Vat(TimeStampMixinModel, UUIDModel):
    id = models.BigAutoField(primary_key=True)
    value = models.DecimalField(
        _("Value"),
        max_digits=11,
        decimal_places=1,
        default=MIN_VAT_VALUE,
        validators=[
            MinValueValidator(
                MIN_VAT_VALUE,
                message=_("VAT value must be at least %(limit_value)s."),
            ),
            MaxValueValidator(
                MAX_VAT_VALUE,
                message=_("VAT value cannot exceed %(limit_value)s."),
            ),
        ],
    )

    class Meta(TypedModelMeta):
        verbose_name = _("Vat")
        verbose_name_plural = _("Vats")
        ordering = ["-created_at"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            BTreeIndex(fields=["value"]),
        ]
        constraints = [
            CheckConstraint(
                condition=Q(value__gte=MIN_VAT_VALUE)
                & Q(value__lte=MAX_VAT_VALUE),
                name="vat_value_range",
            ),
        ]

    def __str__(self):
        return f"{self.value}% VAT"

    @staticmethod
    def get_highest_vat_value():
        highest_vat = Vat.objects.all().order_by("-value").first()
        return highest_vat.value if highest_vat else MIN_VAT_VALUE
