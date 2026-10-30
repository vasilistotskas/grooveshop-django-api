from __future__ import annotations

from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from djmoney.models.fields import MoneyField

from order.enum.payment_transaction import (
    CHARGE_REVERSAL_KINDS,
    PaymentFeeSource,
    PaymentTransactionKind,
)


class PaymentTransaction(models.Model):
    """One movement of money for an order, as a provider reported it.

    Append-only: a row is never updated or deleted, and a correction is
    another row. The order's financial state is DERIVED from these rows
    (``order.payment_ledger.PaymentLedger``), so the amount collected, the
    amount refunded, what can still be refunded and what the customer
    still owes each have one source and cannot drift apart the way the
    old ``metadata["refunds"]`` list could. ``Order.amount_due`` is what
    the order costs, never what was paid.

    The one column written after the row exists is ``fee``: what the
    provider charged for the movement, which it reports later than the
    movement itself (Stripe's balance transaction, Viva's "transaction price
    calculated" event). It is the provider's own figure or nothing: a row
    with no fee is "not reported", never "free", and ``fee_source`` names
    the report it came from.

    ``amount`` is always positive; ``kind`` gives the direction. A REFUND
    or a CHARGEBACK (money a card network took back after a lost dispute)
    names the CHARGE it reverses in ``charge``, so an order paid twice
    (two Viva checkout codes) is refunded payment by payment. The unique
    constraint on ``(provider, provider_reference, kind)`` is what absorbs
    a webhook delivered twice: the provider's own id for the movement is
    the idempotency key, not a flag on the order.
    """

    id = models.BigAutoField(primary_key=True)
    order = models.ForeignKey(
        "order.Order",
        related_name="payment_transactions",
        # A financial record outlives anything else on the order: deleting
        # an order for real has to remove its ledger first, on purpose.
        on_delete=models.PROTECT,
        verbose_name=_("Order"),
    )
    kind = models.CharField(
        _("Kind"),
        max_length=16,
        choices=PaymentTransactionKind.choices,
    )
    amount = MoneyField(
        _("Amount"),
        max_digits=11,
        decimal_places=2,
        help_text=_("Always positive; the kind says which way it moved."),
    )
    provider = models.CharField(
        _("Provider"),
        max_length=32,
        help_text=_(
            "Who moved the money: the payment provider's code, or the "
            "courier's cash-on-delivery code."
        ),
    )
    provider_reference = models.CharField(
        _("Provider reference"),
        max_length=255,
        help_text=_(
            "The provider's own id for this movement (a Stripe PaymentIntent "
            "or refund id, a Viva transaction id, a courier voucher)."
        ),
    )
    fee = MoneyField(
        _("Provider fee"),
        max_digits=11,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=_(
            "What the provider charged for this movement, as the provider "
            "reported it. Empty until reported; never an estimate."
        ),
    )
    fee_source = models.CharField(
        _("Fee source"),
        max_length=32,
        choices=PaymentFeeSource.choices,
        blank=True,
        default="",
        db_default="",
        help_text=_("The provider report the fee was read from."),
    )
    charge = models.ForeignKey(
        "self",
        related_name="refunds",
        null=True,
        blank=True,
        # Rows are never deleted; CASCADE only lets a whole order's ledger
        # be removed in one statement.
        on_delete=models.CASCADE,
        verbose_name=_("Refunded charge"),
        help_text=_(
            "Set on a refund or a chargeback: the charge it took money from."
        ),
    )
    description = models.CharField(
        _("Description"),
        max_length=255,
        blank=True,
        default="",
        help_text=_("The reason given for a refund."),
    )
    created_at = models.DateTimeField(_("Created At"), auto_now_add=True)

    class Meta(TypedModelMeta):
        verbose_name = _("Payment Transaction")
        verbose_name_plural = _("Payment Transactions")
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "provider_reference", "kind"],
                name="unique_payment_transaction_reference",
            ),
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name="payment_transaction_amount_positive",
            ),
            models.CheckConstraint(
                condition=(models.Q(fee__isnull=True) & models.Q(fee_source=""))
                | (models.Q(fee__gte=0) & ~models.Q(fee_source="")),
                name="payment_transaction_fee_names_source",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(kind__in=CHARGE_REVERSAL_KINDS)
                    & models.Q(charge__isnull=False)
                )
                | (
                    ~models.Q(kind__in=CHARGE_REVERSAL_KINDS)
                    & models.Q(charge__isnull=True)
                ),
                name="payment_transaction_reversal_names_charge",
            ),
        ]
        indexes = [
            BTreeIndex(
                fields=["order", "kind"], name="payment_tx_order_kind_ix"
            ),
            BTreeIndex(fields=["created_at"], name="payment_tx_created_ix"),
        ]

    def __str__(self) -> str:
        return gettext("%(kind)s %(amount)s on order %(order)s") % {
            "kind": self.get_kind_display(),
            "amount": self.amount,
            "order": self.order_id,
        }
