import os

from core.fields.image import ImageAndSvgField
from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from djmoney.models.fields import MoneyField
from parler.models import TranslatableModel, TranslatedFields
from tinymce.models import HTMLField

from core.models import SortableModel, TimeStampMixinModel, UUIDModel
from pay_way.enum.pay_way import PayWayEnum
from pay_way.managers import PayWayManager
from shipping.enum import ShippingKind


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
    icon = ImageAndSvgField(
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
        instructions=HTMLField(
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
    def main_image_path(self) -> str:
        if self.icon and hasattr(self.icon, "name"):
            return (
                f"media/uploads/pay_way/{os.path.basename(str(self.icon.name))}"
            )
        return ""

    @property
    def icon_filename(self) -> str:
        if self.icon and hasattr(self.icon, "name"):
            return os.path.basename(str(self.icon.name))
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
    def is_cash_on_delivery(self) -> bool:
        # Why: BoxNow's PAY ON THE GO product (and ACS Acs_Delivery_Products="COD")
        # need to distinguish "courier collects cash/card at the door" from
        # other offline pay-ways (e.g. bank transfer, where the customer pays
        # us directly off-platform and the courier collects nothing).
        # ``is_online_payment=False`` alone is too broad — bank transfer is
        # also offline. The canonical COD case is offline AND not requiring
        # off-platform confirmation: the money changes hands on delivery.
        return not self.is_online_payment and not self.requires_confirmation

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


class PayWayShippingExclusion(TimeStampMixinModel):
    """Per-(shipping provider, kind, pay-way) availability override.

    A row's presence means the pay-way is **disabled** for that
    (provider, kind) combination. An empty table = every active
    pay-way is offered on every supported (provider, kind), which
    matches the pre-existing behaviour without any seed data.

    Two-layer model with :func:`pay_way.services.PayWayService.filter_by_carrier`:

    * **Layer 1 (this table)** — admin-configurable soft rules. Toggle
      from Django admin without a redeploy.
    * **Layer 2** — ``ShippingCarrierInterface.filter_pay_ways`` hard
      vetos in code for cases where the courier API itself genuinely
      rejects a combination regardless of operator preference.

    The two layers compose: exclusions here run BEFORE the carrier
    hook, so an operator-blocked combination short-circuits without
    even consulting the carrier adapter.
    """

    pay_way = models.ForeignKey(
        "pay_way.PayWay",
        on_delete=models.CASCADE,
        related_name="shipping_exclusions",
        verbose_name=_("Pay way"),
    )
    shipping_provider = models.ForeignKey(
        "shipping.ShippingProvider",
        on_delete=models.CASCADE,
        related_name="pay_way_exclusions",
        verbose_name=_("Shipping provider"),
    )
    shipping_kind = models.CharField(
        _("Shipping kind"),
        max_length=32,
        choices=ShippingKind.choices,
    )
    note = models.TextField(
        _("Note"),
        blank=True,
        default="",
        help_text=_(
            "Optional explanation for why this combination is "
            "blocked (e.g. 'PAY ON THE GO not yet active on the "
            "BoxNow partner account — re-enable when ops confirms'). "
            "Visible to admins only — never shown to customers."
        ),
    )

    class Meta(TypedModelMeta):
        verbose_name = _("PayWay shipping exclusion")
        verbose_name_plural = _("PayWay shipping exclusions")
        ordering = ["shipping_provider", "shipping_kind", "pay_way"]
        constraints = [
            models.UniqueConstraint(
                fields=("pay_way", "shipping_provider", "shipping_kind"),
                name="payway_shipping_exclusion_unique",
            ),
        ]
        # Override the inherited TimeStampMixinModel indexes with
        # shorter explicit names: the mixin uses ``%(class)s`` which
        # would resolve to ``paywayshippingexclusion_created_at_ix``
        # (37 chars) and trip Django's ``models.E034`` 30-char index-
        # name limit. We still want the timestamp indexes; just give
        # them tighter names.
        indexes = [
            BTreeIndex(fields=["created_at"], name="payway_excl_created_at_ix"),
            BTreeIndex(fields=["updated_at"], name="payway_excl_updated_at_ix"),
            BTreeIndex(
                fields=["shipping_provider", "shipping_kind"],
                name="payway_excl_provider_kind_ix",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"{self.pay_way} blocked on "
            f"{self.shipping_provider.code}/{self.shipping_kind}"
        )
