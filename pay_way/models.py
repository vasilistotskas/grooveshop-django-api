import os

from django.conf import settings
from django.db import models
from django.db.models.query import QuerySet

from core.models import SortableModel
from core.models import TimeStampMixinModel
from core.models import UUIDModel
from order.enum.pay_way_enum import PayWayEnum


class PayWay(TimeStampMixinModel, SortableModel, UUIDModel):
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=50, choices=PayWayEnum.choices(), unique=True)
    active = models.BooleanField(default=True)
    cost = models.DecimalField(max_digits=11, decimal_places=2, default=0.0)
    free_for_order_amount = models.DecimalField(
        max_digits=11, decimal_places=2, default=0.0
    )
    icon = models.ImageField(upload_to="uploads/pay_way/", blank=True, null=True)

    class Meta:
        verbose_name_plural = "Payment Methods"

    def __str__(self):
        return self.name

    def get_ordering_queryset(self) -> QuerySet:
        return PayWay.objects.all()

    @property
    def icon_absolute_url(self) -> str:
        icon: str = ""
        if self.icon and hasattr(self.icon, "url"):
            return settings.BACKEND_BASE_URL + self.icon.url
        return icon

    @property
    def icon_filename(self) -> str:
        if self.icon and hasattr(self.icon, "name"):
            return os.path.basename(self.icon.name)
        else:
            return ""

    @classmethod
    def active_pay_ways_by_status(cls, status: bool) -> QuerySet:
        return cls.objects.filter(active=status).values()
