import os

from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from djmoney.models.fields import MoneyField
from parler.models import TranslatableModel, TranslatedFields

from core.models import SortableModel, TimeStampMixinModel, UUIDModel
from pay_way.enum.pay_way import PayWayEnum
from pay_way.managers import PayWayManager


class PayWay(TranslatableModel, TimeStampMixinModel, SortableModel, UUIDModel):
    id = models.BigAutoField(primary_key=True)
    active = models.BooleanField(_("Active"), default=True)
    cost = MoneyField(
        _("Cost"),
        max_digits=11,
        decimal_places=2,
        default=0,
    )
    free_threshold = MoneyField(
        _("Free Threshold"),
        max_digits=11,
        decimal_places=2,
        default=0,
        help_text=_(
            "Order amount above which this payment method becomes free"
        ),
    )
    icon = models.ImageField(
        _("Icon"), upload_to="uploads/pay_way/", blank=True, null=True
    )
    provider_code = models.CharField(
        _("Provider Code"),
        max_length=50,
        blank=True,
        default="",
        help_text=_(
            "Code used to identify the payment provider in the system (e.g., 'stripe', 'paypal')"
        ),
    )
    is_online_payment = models.BooleanField(
        _("Is Online Payment"),
        default=False,
        help_text=_("Whether this payment method is processed online"),
    )
    requires_confirmation = models.BooleanField(
        _("Requires Confirmation"),
        default=False,
        help_text=_(
            "Whether this payment method requires manual confirmation (e.g., bank transfer)"
        ),
    )
    configuration = models.JSONField(
        _("Provider Configuration"),
        blank=True,
        null=True,
        help_text=_(
            "Provider-specific configuration (API keys, webhooks, etc.)"
        ),
    )
    translations = TranslatedFields(
        name=models.CharField(
            _("Name"),
            max_length=50,
            blank=True,
            null=True,
            choices=PayWayEnum,
        ),
        description=models.TextField(_("Description"), blank=True, null=True),
        instructions=models.TextField(
            _("Payment Instructions"),
            blank=True,
            null=True,
            help_text=_(
                "Instructions for the customer on how to complete payment (e.g., bank transfer details)"
            ),
        ),
    )

    objects: PayWayManager = PayWayManager()

    class Meta(TypedModelMeta):
        verbose_name = _("Pay Way")
        verbose_name_plural = _("Pay Ways")
        ordering = ["sort_order"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            *SortableModel.Meta.indexes,
            BTreeIndex(fields=["active"], name="pay_way_active_ix"),
            BTreeIndex(fields=["cost"], name="pay_way_cost_ix"),
            BTreeIndex(
                fields=["free_threshold"], name="pay_way_free_threshold_ix"
            ),
            BTreeIndex(fields=["provider_code"], name="pay_way_provider_ix"),
            BTreeIndex(fields=["is_online_payment"], name="pay_way_online_ix"),
        ]

    def __str__(self):
        return self.safe_translation_getter("name", any_language=True) or ""

    def get_ordering_queryset(self):
        return PayWay.objects.all()

    @property
    def icon_filename(self) -> str:
        if self.icon and hasattr(self.icon, "name"):
            return os.path.basename(self.icon.name)
        else:
            return ""

    @property
    def display_name(self) -> str:
        return self.safe_translation_getter("name", any_language=True) or ""

    @property
    def has_configuration(self) -> bool:
        return bool(self.configuration)

    @property
    def is_configured(self) -> bool:
        if not self.is_online_payment:
            return True
        return self.has_configuration

    @property
    def effective_cost(self) -> float:
        return float(self.cost.amount) if self.cost else 0.0

    def is_free_for_amount(self, amount: float) -> bool:
        if not self.free_threshold:
            return False
        return amount >= float(self.free_threshold.amount)

    def get_configuration_value(self, key: str, default=None):
        if not self.configuration:
            return default
        return self.configuration.get(key, default)

    def set_configuration_value(self, key: str, value) -> None:
        if not self.configuration:
            self.configuration = {}
        self.configuration[key] = value
