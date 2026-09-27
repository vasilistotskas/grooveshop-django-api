from decimal import Decimal

from django.contrib.postgres.indexes import BTreeIndex
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from djmoney.models.fields import MoneyField
from simple_history.models import HistoricalRecords

from core.models import TimeStampMixinModel
from shipping.enum import ShippingKind


class ShippingRate(TimeStampMixinModel):
    """Single source of truth for a store's shipping price + availability.

    One row per (provider, country, kind): the price the shopper pays,
    the subtotal above which the combination ships free, and the
    weight cap the carrier will accept. Country/Region are global ISO
    reference data — this table is what turns "Cyprus exists" into
    "this store ships to Cyprus", per tenant, per carrier.

    Lives in the tenant schema (unlike ``country.Country``, which is
    SHARED_APPS/public) — ``country`` below is a cross-schema FK, the
    same shape ``Order.country`` already uses.
    """

    provider = models.ForeignKey(
        "shipping.ShippingProvider",
        related_name="rates",
        on_delete=models.CASCADE,
    )
    # Cross-schema FK to the public ``country.Country`` row.
    # ``PROTECT``: a country referenced by a rate must be removed from
    # the rate first — silently cascading would delete pricing data a
    # deleted-but-still-referenced order might need to reconstruct.
    # ``related_name="+"``: no reverse accessor, matching ``Order.country``
    # — a public-schema model has no business exposing a reverse relation
    # into every tenant schema's rates.
    country = models.ForeignKey(
        "country.Country",
        related_name="+",
        on_delete=models.PROTECT,
    )
    kind = models.CharField(
        _("Shipping Kind"),
        max_length=32,
        choices=ShippingKind.choices,
    )
    price = MoneyField(
        _("Price"),
        max_digits=11,
        decimal_places=2,
        default=0,
        help_text=_(
            "What the shopper pays for this (provider, country, kind), "
            "before ``free_shipping_threshold`` or a carrier's own "
            "``live_quote`` override it."
        ),
    )
    free_shipping_threshold = MoneyField(
        _("Free Shipping Threshold"),
        max_digits=11,
        decimal_places=2,
        null=True,
        blank=True,
        default=None,
        help_text=_(
            "Cart subtotal above which this combination ships free. "
            "Blank means this combination is never free."
        ),
    )
    max_weight_grams = models.PositiveIntegerField(
        _("Max Weight (grams)"),
        null=True,
        blank=True,
        default=None,
        help_text=_(
            "Heaviest parcel this combination will carry. Blank means "
            "no cap. A cart over the cap still lists the option "
            "(flagged ``exceeds_max_weight``) rather than disappearing "
            "silently."
        ),
    )
    is_active = models.BooleanField(
        _("Is Active"),
        default=True,
        db_default=True,
        help_text=_(
            "Master switch for this (provider, country, kind) — off "
            "hides it from checkout regardless of the provider's own "
            "``is_active``."
        ),
    )

    # Audit trail for price changes — a merchant support ticket about
    # "why did shipping to Cyprus jump" is answered from this table
    # rather than a support engineer guessing from memory.
    history = HistoricalRecords(user_db_constraint=False)

    class Meta(TypedModelMeta):
        verbose_name = _("Shipping Rate")
        verbose_name_plural = _("Shipping Rates")
        ordering = ["provider__priority", "country_id", "kind"]
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "country", "kind"],
                name="shipping_rate_provider_country_kind_unique",
            ),
        ]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            BTreeIndex(
                fields=["country", "is_active"],
                name="shipping_rate_country_active_ix",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.provider.code}/{self.country_id}/{self.kind}"

    def clean(self) -> None:
        super().clean()
        errors: dict[str, list[str]] = {}

        if self.provider_id and not self.provider.supports(self.kind):
            errors.setdefault("kind", []).append(
                str(
                    _("%(provider)s does not support %(kind)s shipments.")
                    % {
                        "provider": self.provider.code,
                        "kind": self.kind,
                    }
                )
            )

        if self.price is not None and self.price.amount < 0:
            errors.setdefault("price", []).append(
                str(_("Price cannot be negative."))
            )

        from django.conf import settings as django_settings
        from django.db import connection

        tenant_currency = (
            getattr(connection.tenant, "default_currency", None)
            if getattr(connection, "tenant", None) is not None
            else None
        ) or getattr(django_settings, "DEFAULT_CURRENCY", "EUR")

        for field_name, money in (
            ("price", self.price),
            ("free_shipping_threshold", self.free_shipping_threshold),
        ):
            if money is not None and str(money.currency) != tenant_currency:
                errors.setdefault(field_name, []).append(
                    str(
                        _(
                            "Currency must match the store's own "
                            "currency (%(currency)s)."
                        )
                        % {"currency": tenant_currency}
                    )
                )

        if errors:
            raise ValidationError(errors)

    @property
    def effective_price(self) -> Decimal:
        return self.price.amount if self.price else Decimal(0)
