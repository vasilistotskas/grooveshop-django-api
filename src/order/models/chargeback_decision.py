"""What staff decided about a lost dispute: did the goods come back?

ν.4308/2014 art. 12(4): a retail credit document (11.4) is issued "in the
case of discounts or refunds". A lost dispute is the card network taking
money back, not the merchant reversing the sale, so by itself it owes no
credit; the sale stands when the customer kept the goods. When the goods
came back (or were never delivered) the sale is reversed in fact and the
receipt must be corrected like any refund. Nothing on the ledger can tell
the two apart, so a person says, once, and this row records it.
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta


class ChargebackDecision(models.Model):
    """The decision on one CHARGEBACK row of the payment ledger.

    No row means the dispute still awaits a decision (the dashboard lists
    it). ``decided_by`` and ``decided_at`` are the audit; every change is
    also written to the order's history. A decision that the goods came
    back drives a credit document (``order.credit_documents``); one that
    the customer kept them changes nothing.
    """

    id = models.BigAutoField(primary_key=True)
    chargeback = models.OneToOneField(
        "order.PaymentTransaction",
        related_name="decision",
        on_delete=models.PROTECT,
        verbose_name=_("Chargeback"),
    )
    goods_returned = models.BooleanField(
        _("Goods returned"),
        help_text=_(
            "Yes: the goods came back or were never delivered, so the sale "
            "is reversed and the receipt is credited. No: the customer "
            "kept the goods and the sale stands."
        ),
    )
    decided_by = models.ForeignKey(
        "user.UserAccount",
        related_name="+",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        # ORM-only, like StockLog.performed_by: the staff member is a
        # PLATFORM-PUBLIC identity (the only way into a store's admin) and
        # this row lives in the tenant schema, where a real FK would
        # either violate or match an unrelated shopper with the same pk.
        db_constraint=False,
        verbose_name=_("Decided by"),
        help_text=_("Stores a PLATFORM-schema user id; not FK-enforced."),
    )
    decided_at = models.DateTimeField(
        _("Decided at"),
        default=timezone.now,
        help_text=_(
            "When the decision last took its current value. A receipt "
            "issued after it is reduced by the chargeback; one issued "
            "before it is credited."
        ),
    )

    class Meta(TypedModelMeta):
        verbose_name = _("Chargeback Decision")
        verbose_name_plural = _("Chargeback Decisions")

    def __str__(self) -> str:
        return gettext("%(outcome)s (chargeback %(chargeback)s)") % {
            "outcome": gettext("Goods returned")
            if self.goods_returned
            else gettext("Customer kept the goods"),
            "chargeback": self.chargeback_id,
        }
