from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from djmoney.models.fields import MoneyField
from simple_history.models import HistoricalRecords

from core.models import TimeStampMixinModel
from shipping.enum import ShippingKind
from tenant.staff_actors import history_user_getter, history_user_setter


class ShippingRate(TimeStampMixinModel):
    """Single source of truth for a store's shipping price + availability.

    One row per (provider, zone, kind): the price the shopper pays, the
    subtotal above which the combination ships free, and the weight cap
    the carrier will accept. Country/Region are global ISO reference
    data; a ``ShippingZone`` and its rates are what turn "Cyprus exists"
    into "this store ships to Cyprus", per tenant, per carrier, and
    "Crete exists" into "this store charges more for Crete".

    A pickup-point rate belongs to a zone covering a whole country: a
    pickup point has no region (``shipping.zones``).
    """

    provider = models.ForeignKey(
        "shipping.ShippingProvider",
        related_name="rates",
        on_delete=models.CASCADE,
        verbose_name=_("Shipping Provider"),
    )
    # The destination this price applies to: a whole country, or the
    # regions of one (``ShippingZone``). Which countries and regions a
    # store serves is a question of its zones and their rates.
    zone = models.ForeignKey(
        "shipping.ShippingZone",
        related_name="rates",
        on_delete=models.CASCADE,
        verbose_name=_("Shipping Zone"),
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
            "What the shopper pays for this (provider, zone, kind), "
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
            "Ships free once the products, after discounts and excluding "
            "shipping, reach this amount. The payment-method fee is "
            "waived on the same value. Blank means this combination is "
            "never free."
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
    delivery_days_min = models.PositiveSmallIntegerField(
        _("Delivery Days (min)"),
        null=True,
        blank=True,
        default=None,
        help_text=_(
            "Fastest delivery, in business days after dispatch. Blank "
            "means no estimate is advertised for this combination; set "
            "both bounds (equal for a fixed promise)."
        ),
    )
    delivery_days_max = models.PositiveSmallIntegerField(
        _("Delivery Days (max)"),
        null=True,
        blank=True,
        default=None,
        help_text=_(
            "Slowest delivery, in business days after dispatch. The "
            "order's ``estimated_delivery`` date is dispatch plus this "
            "many business days. Blank with the minimum also blank."
        ),
    )
    is_active = models.BooleanField(
        _("Is Active"),
        default=True,
        db_default=True,
        help_text=_(
            "Master switch for this (provider, zone, kind) — off "
            "hides it from checkout regardless of the provider's own "
            "``is_active``."
        ),
    )

    # Audit trail for price changes — a merchant support ticket about
    # "why did shipping to Cyprus jump" is answered from this table
    # rather than a support engineer guessing from memory.
    history = HistoricalRecords(
        history_user_id_field=models.BigIntegerField(null=True),
        history_user_getter=history_user_getter,
        history_user_setter=history_user_setter,
    )

    class Meta(TypedModelMeta):
        verbose_name = _("Shipping Rate")
        verbose_name_plural = _("Shipping Rates")
        ordering = ["provider__priority", "zone_id", "kind"]
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "zone", "kind"],
                name="shipping_rate_provider_zone_kind_unique",
            ),
        ]
        indexes = [*TimeStampMixinModel.Meta.indexes]

    def __str__(self) -> str:
        return f"{self.provider.code}/{self.zone_id}/{self.kind}"

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

        if (self.delivery_days_min is None) != (self.delivery_days_max is None):
            field = (
                "delivery_days_max"
                if self.delivery_days_max is None
                else "delivery_days_min"
            )
            errors.setdefault(field, []).append(
                str(_("Set both delivery bounds or neither."))
            )
        elif (
            self.delivery_days_min is not None
            and self.delivery_days_max is not None
            and self.delivery_days_min > self.delivery_days_max
        ):
            errors.setdefault("delivery_days_max", []).append(
                str(_("Maximum cannot be below the minimum."))
            )

        if self.price is not None and self.price.amount < 0:
            errors.setdefault("price", []).append(
                str(_("Price cannot be negative."))
            )

        from django.conf import settings as django_settings

        from tenant.membership import bound_tenant

        tenant_currency = getattr(
            bound_tenant(), "default_currency", None
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
