"""Product back-in-stock and price-drop subscription alerts.

Complements the existing favourite-based price-drop notifications: a user
(or guest via email) can subscribe to a specific product and be notified
when it comes back in stock, or when its price drops below a chosen
target. Unlike favourites, subscriptions are single-shot — once an alert
fires, `notified_at` is set and the subscription deactivates so we never
spam.
"""

from __future__ import annotations

from django.contrib.postgres.indexes import BTreeIndex
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from djmoney.models.fields import MoneyField

from core.models import TimeStampMixinModel, UUIDModel


class ProductAlertKind(models.TextChoices):
    RESTOCK = "restock", _("Restock")
    PRICE_DROP = "price_drop", _("Price drop")


class ProductAlert(TimeStampMixinModel, UUIDModel):
    """A user- or email-bound, single-shot product alert subscription."""

    id = models.BigAutoField(primary_key=True)
    kind = models.CharField(
        _("Kind"),
        max_length=16,
        choices=ProductAlertKind.choices,
    )
    product = models.ForeignKey(
        "product.Product",
        on_delete=models.CASCADE,
        related_name="alerts",
    )
    user = models.ForeignKey(
        "user.UserAccount",
        on_delete=models.CASCADE,
        related_name="product_alerts",
        null=True,
        blank=True,
    )
    email = models.EmailField(_("Email"), blank=True, default="")
    target_price = MoneyField(
        _("Target price"),
        max_digits=11,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=_(
            "For price_drop alerts: trigger when the product's price "
            "falls to or below this value. Ignored for restock alerts."
        ),
    )
    is_active = models.BooleanField(_("Is active"), default=True)
    notified_at = models.DateTimeField(_("Notified at"), null=True, blank=True)

    class Meta(TypedModelMeta):
        verbose_name = _("Product alert")
        verbose_name_plural = _("Product alerts")
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["kind", "product", "user"],
                condition=models.Q(user__isnull=False),
                name="unique_product_alert_user",
            ),
            models.UniqueConstraint(
                fields=["kind", "product", "email"],
                condition=models.Q(user__isnull=True) & ~models.Q(email=""),
                name="unique_product_alert_email",
            ),
            models.CheckConstraint(
                condition=models.Q(user__isnull=False) | ~models.Q(email=""),
                name="product_alert_has_recipient",
            ),
        ]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            BTreeIndex(
                fields=["product", "kind", "is_active"],
                name="product_alert_active_ix",
            ),
        ]

    def __str__(self) -> str:
        recipient = (
            self.user.email if self.user_id else self.email or "<anonymous>"
        )
        return f"{self.get_kind_display()} — {recipient} — product {self.product_id}"

    def clean(self) -> None:
        super().clean()
        if not self.user_id and not self.email:
            raise ValidationError(
                _("Either a user or an email must be provided.")
            )
        if self.kind == ProductAlertKind.PRICE_DROP and not self.target_price:
            raise ValidationError(
                {
                    "target_price": _(
                        "target_price is required for price-drop alerts."
                    )
                }
            )
