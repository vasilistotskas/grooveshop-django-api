from django.db import models

from core.models import SortableModel
from core.models import TimeStampMixinModel
from core.models import UUIDModel


class Country(TimeStampMixinModel, SortableModel, UUIDModel):
    alpha_2 = models.CharField(max_length=2, primary_key=True, unique=True)
    alpha_3 = models.CharField(max_length=3, unique=True)
    name = models.CharField(max_length=50, unique=True)
    iso_cc = models.PositiveSmallIntegerField(blank=True, null=True, unique=True)
    phone_code = models.PositiveSmallIntegerField(blank=True, null=True, unique=True)
    image_flag = models.ImageField(blank=True, null=True, upload_to="uploads/country/")

    class Meta:
        verbose_name_plural = "Countries"

    def __str__(self):
        return self.name

    def get_ordering_queryset(self):
        return Country.objects.all()
