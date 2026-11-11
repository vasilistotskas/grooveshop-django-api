"""One taxable line of an order as it was when the order was created.

``order.taxation.order_vat_lines`` derives the lines the receipt and the
myDATA payload bill; this row keeps them. The turnover report, the store
dashboard and every other sales figure read these rows instead of
re-deriving the split in SQL, so a figure cannot disagree with the receipt
because a second implementation of the rule drifted. The rows are written
once, when the order is created (``order.vat_snapshot``), and are not
rewritten by a later change to a product, a VAT row or a promotion.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import models
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta

from order.taxation import ChargeKind
from vat.models import validate_mydata_vat_rate


class OrderVatLine(models.Model):
    """The net and the VAT of one goods, shipping or fee line.

    Amounts are in the order's currency (one currency per store). A line
    that bills nothing has no row, as on the receipt.
    """

    id = models.BigAutoField(primary_key=True)
    order = models.ForeignKey(
        "order.Order",
        related_name="vat_lines",
        on_delete=models.CASCADE,
        verbose_name=_("Order"),
    )
    item = models.ForeignKey(
        "order.OrderItem",
        related_name="vat_lines",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        verbose_name=_("Order item"),
        help_text=_("The line a goods row was taken from; empty otherwise."),
    )
    kind = models.CharField(
        _("Kind"), max_length=20, choices=ChargeKind.choices
    )
    vat_rate = models.DecimalField(
        _("VAT Rate"),
        max_digits=11,
        decimal_places=1,
        validators=[validate_mydata_vat_rate],
    )
    net = models.DecimalField(_("Net"), max_digits=11, decimal_places=2)
    vat = models.DecimalField(_("VAT"), max_digits=11, decimal_places=2)

    class Meta(TypedModelMeta):
        verbose_name = _("Order VAT Line")
        verbose_name_plural = _("Order VAT Lines")
        ordering = ["order_id", "id"]

    def __str__(self) -> str:
        return gettext("Order %(order)s - %(kind)s %(rate)s%%") % {
            "order": self.order_id,
            "kind": self.get_kind_display(),
            "rate": self.vat_rate,
        }

    @property
    def gross(self) -> Decimal:
        return self.net + self.vat
