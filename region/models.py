from django.db import models

from core.models import SortableModel
from core.models import TimeStampMixinModel
from core.models import UUIDModel


class Region(TimeStampMixinModel, SortableModel, UUIDModel):
    alpha = models.CharField(max_length=10, primary_key=True, unique=True)
    alpha_2 = models.ForeignKey(
        "country.Country", related_name="region_alpha_2", on_delete=models.CASCADE
    )
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        verbose_name_plural = "Regions"

    def __str__(self):
        return self.name

    def get_ordering_queryset(self):
        return Region.objects.all()
