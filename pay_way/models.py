import os

from django.conf import settings
from django.db import models
from django.db.models.query import QuerySet
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from djmoney.models.fields import MoneyField
from parler.models import TranslatableModel
from parler.models import TranslatedFields

from core.models import SortableModel
from core.models import TimeStampMixinModel
from core.models import UUIDModel
from pay_way.enum.pay_way_enum import PayWayEnum


class PayWay(TranslatableModel, TimeStampMixinModel, SortableModel, UUIDModel):
    id = models.BigAutoField(primary_key=True)
    active = models.BooleanField(_("Active"), default=True)
    cost = MoneyField(_("Cost"), max_digits=19, decimal_places=4, default=0)
    free_for_order_amount = MoneyField(
        _("Free For Order Amount"), max_digits=19, decimal_places=4, default=0
    )
    icon = models.ImageField(
        _("Icon"), upload_to="uploads/pay_way/", blank=True, null=True
    )
    translations = TranslatedFields(
        name=models.CharField(
            _("Name"),
            max_length=50,
            blank=True,
            null=True,
            choices=PayWayEnum.choices,
        )
    )

    class Meta(TypedModelMeta):
        verbose_name = _("Pay Way")
        verbose_name_plural = _("Pay Ways")
        ordering = ["sort_order"]

    def __unicode__(self):
        return self.safe_translation_getter("name", any_language=True) or ""

    def __str__(self):
        return self.safe_translation_getter("name", any_language=True) or ""

    def get_ordering_queryset(self) -> QuerySet:
        return PayWay.objects.all()

    @property
    def icon_absolute_url(self) -> str:
        icon: str = ""
        if self.icon and hasattr(self.icon, "url"):
            return settings.APP_BASE_URL + self.icon.url
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
