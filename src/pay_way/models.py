import os

from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from djmoney.models.fields import MoneyField
from parler.models import TranslatableModel, TranslatedFields

from core.fields.image import ImageAndSvgField
from core.fields.rich_text import RichTextField
from core.models import SortableModel, TimeStampMixinModel, UUIDModel
from pay_way.enum.pay_way import PayWayEnum
from pay_way.enum.settlement import PaySettlement
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
            "The payment method is free once the products, after "
            "discounts and excluding shipping, reach this amount. "
            "Free shipping is judged on the same value."
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
    settlement = models.CharField(
        _("Settlement"),
        max_length=32,
        choices=PaySettlement.choices,
        default=PaySettlement.ONLINE,
        help_text=_(
            "How the money changes hands. This is the authoritative "
            "discriminator for the shipping layer: a carrier declares "
            "which settlements it can physically perform, and the "
            "voucher's payment mode derives from it. Do not re-derive "
            "it from the deprecated booleans below."
        ),
    )
    key = models.CharField(
        _("Key"),
        max_length=50,
        # ``db_default`` keeps the release still serving during the
        # PreSync rollout able to INSERT (it never names this column);
        # a row without a key renders no label on the storefront, so
        # forms and the write serializer require one.
        default="",
        db_default="",
        choices=PayWayEnum.choices,
        help_text=_(
            "Language-independent identifier of the payment method. The "
            "storefront resolves its label from this key, so it never "
            "depends on which languages the store has translated."
        ),
    )
    translations = TranslatedFields(
        description=models.TextField(_("Description"), blank=True, null=True),
        instructions=RichTextField(
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
            BTreeIndex(fields=["settlement"], name="pay_way_settlement_ix"),
        ]

    @property
    def display_name(self) -> str:
        """Human-readable label for this payment method.

        ``key`` deliberately stores a ``PayWayEnum`` KEY rather than a
        display string — one shared vocabulary across the two repos,
        seedable by a migration without knowing a language. The
        consequence is that every Django-rendered surface that printed
        the raw column showed ``PAY_ON_DELIVERY``: the invoice PDF's
        "Method" line, the admin order email, the admin list and every
        autocomplete label. This is the one place that resolves the key.

        Resolution mirrors Django's ``get_FOO_display()``: a known
        member yields its ``gettext_lazy`` label, anything else yields
        the stored value unchanged. Blank stays blank so callers can
        fall back.

        NOT for storefront JSON. A label resolved here is in whatever
        language is active — the request's on the API
        (``core.middleware.locale.RequestLanguageMiddleware``), the
        recipient's in an email — and a storefront that owns its own
        payment-method labels needs the stable key instead. The order
        serializer exposes ``pay_way_key`` for that; see
        ``Order.pay_way_key``.
        """
        if not self.key:
            return ""
        try:
            return str(PayWayEnum(self.key).label)
        except ValueError:
            return self.key

    def __str__(self):
        return self.display_name

    def get_ordering_queryset(self):
        return PayWay.objects.all()

    @property
    def main_image_path(self) -> str:
        from core.utils.image_paths import image_to_media_path

        return image_to_media_path(self.icon)

    @property
    def icon_filename(self) -> str:
        if self.icon and hasattr(self.icon, "name"):
            return os.path.basename(str(self.icon.name))
        else:
            return ""

    @property
    def is_collected_on_delivery(self) -> bool:
        """The carrier collects money from the shopper on delivery.

        True for BOTH courier cash-on-delivery and carrier-terminal
        payment, so it answers "does the voucher need a non-zero
        amount?" — and nothing else. It deliberately cannot tell the
        two products apart; ask ``settlement`` for that.

        Replaces the old ``is_cash_on_delivery``, which conflated them
        and let a generic courier-COD pay-way mint a BoxNow locker
        voucher (charging a cash-handling fee for a terminal that takes
        no cash). See ``PaySettlement``.
        """
        return PaySettlement(self.settlement) in (
            PaySettlement.collected_on_delivery()
        )

    @property
    def effective_cost(self) -> float:
        return float(self.cost.amount) if self.cost else 0.0


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

    ``country`` scopes a row to one delivery country. ``NULL`` means
    every country (the original behaviour, so rows that predate the
    column keep working); a country means that country only. This is
    how a store can offer BoxNow PAY ON THE GO in Greece yet keep a
    Cyprus locker order card-only.
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
    # Cross-schema FK to the public ``country.Country`` row, same shape
    # as ``ShippingRate.country``: no reverse accessor into every tenant
    # schema, PROTECT so a referenced country cannot vanish silently.
    country = models.ForeignKey(
        "country.Country",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_("Country"),
        help_text=_(
            "Delivery country this exclusion applies to. Leave empty to "
            "exclude the pay way for every country."
        ),
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
                fields=(
                    "pay_way",
                    "shipping_provider",
                    "shipping_kind",
                    "country",
                ),
                name="payway_shipping_exclusion_unique",
                # One "every country" row per combination stays unique.
                nulls_distinct=False,
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
        return gettext("%(pay_way)s blocked on %(provider)s/%(kind)s") % {
            "pay_way": self.pay_way,
            "provider": self.shipping_provider.code,
            "kind": self.shipping_kind,
        }
