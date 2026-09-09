import os
import re

from django.contrib.postgres.indexes import BTreeIndex
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from djmoney.models.fields import MoneyField
from parler.models import TranslatableModel, TranslatedFields
from tinymce.models import HTMLField

from core.fields.image import ImageAndSvgField
from core.models import SortableModel, TimeStampMixinModel, UUIDModel
from pay_way.enum.pay_way import PayWayEnum
from pay_way.enum.settlement import PaySettlement
from pay_way.managers import PayWayManager
from shipping.enum import ShippingKind

# Secrets MUST live on the Tenant
# model (stripe_secret_key, viva_wallet_*, acs_*, box_now_*,
# meta_capi_*, etc.) rather than in the unencrypted
# PayWay.configuration JSONField.
# ``clean()`` rejects keys whose name matches a secret-shaped pattern
# so misconfiguration via admin or fixtures is caught at save time.
_SECRET_KEY_PATTERN = re.compile(
    r"(api[_-]?key|secret|password|token|private[_-]?key|"
    r"client[_-]?secret|webhook[_-]?secret)",
    re.IGNORECASE,
)


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
    settlement = models.CharField(
        _("Settlement"),
        max_length=32,
        choices=PaySettlement,
        default=PaySettlement.ONLINE,
        help_text=_(
            "How the money changes hands. This is the authoritative "
            "discriminator for the shipping layer: a carrier declares "
            "which settlements it can physically perform, and the "
            "voucher's payment mode derives from it. Do not re-derive "
            "it from the deprecated booleans below."
        ),
    )
    # DEPRECATED — superseded by ``settlement``; kept for exactly one
    # release so a PreSync migration can land while the previous pods
    # are still serving (expand/contract; migrations must be
    # backwards-compatible or split). ``save()`` keeps them in lockstep
    # with ``settlement`` so nothing can drift in the meantime, and the
    # follow-up migration drops the columns.
    #
    # Read ``settlement`` in new code. Nothing should WRITE these.
    is_online_payment = models.BooleanField(
        _("Is Online Payment"),
        default=False,
        help_text=_(
            "Deprecated mirror of ``settlement == ONLINE``. Dropped in "
            "the release after settlement lands."
        ),
    )
    requires_confirmation = models.BooleanField(
        _("Requires Confirmation"),
        default=False,
        help_text=_(
            "Deprecated mirror of ``settlement == OFFLINE_TRANSFER``. "
            "Dropped in the release after settlement lands."
        ),
    )
    configuration = models.JSONField(
        _("Provider Configuration"),
        blank=True,
        null=True,
        help_text=_(
            "Provider-specific non-secret configuration only "
            "(display options, callback URLs, feature flags). "
            "Secrets — API keys, webhook secrets, OAuth client_secrets — "
            "live on the Tenant model fields (stripe_secret_key, "
            "viva_wallet_*, acs_*, box_now_*, meta_capi_*) so they can "
            "be scoped per-tenant and rotated independently. Keys "
            "matching common secret patterns are rejected at save time."
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
            BTreeIndex(fields=["settlement"], name="pay_way_settlement_ix"),
        ]

    @property
    def display_name(self) -> str:
        """Human-readable label for this payment method.

        ``PayWayTranslation.name`` deliberately stores a ``PayWayEnum``
        KEY rather than a display string — one shared vocabulary across
        the two repos, seedable by a migration without knowing a
        language. The consequence is that every Django-rendered surface
        that printed the raw column showed ``PAY_ON_DELIVERY``: the
        invoice PDF's "Method" line, the admin order email, the admin
        list and every autocomplete label. This is the one place that
        resolves the key.

        Resolution mirrors Django's ``get_FOO_display()``: a known
        member yields its ``gettext_lazy`` label, anything else yields
        the stored value unchanged. Blank stays blank so callers can
        fall back.

        NOT for storefront JSON. The API is pinned to ``el`` — every
        route lives under ``i18n_patterns(prefix_default_language=
        False)``, and Django's ``LocaleMiddleware`` forces
        ``settings.LANGUAGE_CODE`` for any path without a language
        prefix — so a label resolved here is always Greek regardless of
        the caller's ``Accept-Language``. That is correct for the
        surfaces above (staff and Greek customers) and wrong for a
        storefront that translates client-side. The order serializer
        exposes ``pay_way_key`` for that; see ``Order.pay_way_key``.
        """
        raw = self.safe_translation_getter("name", any_language=True) or ""
        if not raw:
            return ""
        try:
            return str(PayWayEnum(raw).label)
        except ValueError:
            return raw

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
    def has_configuration(self) -> bool:
        return bool(self.configuration)

    @property
    def is_configured(self) -> bool:
        # Only a provider that actually charges needs credentials;
        # a settlement collected later has nothing to configure.
        if PaySettlement(self.settlement) != PaySettlement.ONLINE:
            return True
        return self.has_configuration

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

    def save(self, *args, **kwargs):
        """Keep the deprecated boolean mirrors in lockstep.

        ``settlement`` is the only stored truth. The two booleans still
        exist for one release (expand/contract — the PreSync migration
        lands while the previous pods are still reading them), so they
        are derived here rather than set by hand. Without this, a row
        edited after the migration would disagree with itself for as
        long as the old pods served traffic.

        Both are added to ``update_fields`` when a caller passed one,
        or the mirrors silently stop tracking on partial saves — the
        same trap ``Order.save()`` had to fix for ``updated_at``.
        """
        settlement = PaySettlement(self.settlement)
        self.is_online_payment = settlement == PaySettlement.ONLINE
        self.requires_confirmation = (
            settlement == PaySettlement.OFFLINE_TRANSFER
        )

        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = {
                *update_fields,
                "is_online_payment",
                "requires_confirmation",
            }

        super().save(*args, **kwargs)

    def clean(self) -> None:
        super().clean()
        if not self.configuration:
            return
        if not isinstance(self.configuration, dict):
            raise ValidationError(
                {"configuration": _("Configuration must be a JSON object.")}
            )
        bad_keys = [
            key
            for key in self.configuration
            if _SECRET_KEY_PATTERN.search(str(key))
        ]
        if bad_keys:
            raise ValidationError(
                {
                    "configuration": _(
                        "Secrets are not allowed in PayWay configuration. "
                        "Move these keys to the Tenant model: %(keys)s"
                    )
                    % {"keys": ", ".join(bad_keys)}
                }
            )


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
