"""The legal record of what a product cost the consumer, and when.

EU Omnibus: an announced price reduction must state the lowest price of
the 30 days before it started. ``HistoricalRecords`` cannot answer that:
it is purged after 365 days, snapshots no VAT rate, and knows nothing
about the automatic promotions that lower a price without saving the
product. A spell is one period during which the consumer gross unit
price was constant.

Append-only. The only mutation is closing the current spell (setting
``valid_to``) when the price changes, and nothing deletes one: they are
not covered by ``clean_old_history``, which only touches simple-history
tables. ``product.pricing.service`` is the one writer.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.db.models import CheckConstraint, Q, UniqueConstraint
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from djmoney.models.fields import MoneyField


class ProductPriceSpell(models.Model):
    id = models.BigAutoField(primary_key=True)
    product = models.ForeignKey(
        "product.Product",
        on_delete=models.CASCADE,
        related_name="price_spells",
        verbose_name=_("Product"),
    )
    price = MoneyField(
        _("Consumer price"),
        max_digits=11,
        decimal_places=2,
        default_currency=settings.DEFAULT_CURRENCY,
        currency_choices=[
            (settings.DEFAULT_CURRENCY, settings.DEFAULT_CURRENCY)
        ],
        help_text=_(
            "The gross unit price a consumer paid: price plus VAT, minus "
            "the product markdown and any unconditional automatic promotion."
        ),
    )
    valid_from = models.DateTimeField(_("Valid from"))
    valid_to = models.DateTimeField(
        _("Valid to"),
        null=True,
        blank=True,
        help_text=_("Empty while this is the current price."),
    )

    class Meta(TypedModelMeta):
        verbose_name = _("Product price spell")
        verbose_name_plural = _("Product price spells")
        ordering = ["product_id", "valid_from"]
        indexes = [
            BTreeIndex(
                fields=["product", "valid_from"], name="pricespell_prod_from_ix"
            ),
            BTreeIndex(
                fields=["product", "valid_to"], name="pricespell_prod_to_ix"
            ),
        ]
        constraints = [
            UniqueConstraint(
                fields=["product"],
                condition=Q(valid_to__isnull=True),
                name="pricespell_one_current_per_product",
            ),
            CheckConstraint(
                condition=Q(valid_to__isnull=True)
                | Q(valid_to__gte=models.F("valid_from")),
                name="pricespell_valid_range",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.product_id} {self.price} from {self.valid_from:%Y-%m-%d %H:%M}"
