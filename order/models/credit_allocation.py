"""Where a refund (or a returned chargeback) sits on the receipt's VAT rates.

A retail credit document (myDATA 11.4) must credit exactly the VAT rates its
refund belongs to, and AADE cannot check it against the receipt it credits
(an 11.4 may not carry ``correlatedInvoices``, error 205), so the platform's
own books are the only guard. They used to be a prediction: a credit was
spread over the rates when it was ISSUED, in whatever order the refunds
happened to settle, so a refund claimed earlier could find its room taken.

Now the split is an invariant stored when the money is CLAIMED, under the
order lock. Every source of a credit owns its rows here:

* a refund attempt (a share of a refund, claimed before the provider is
  called): the rates of the lines it pays for, or, for a bare (goodwill)
  amount, the amount spread over the headroom left at that moment;
* a booked refund ledger row no attempt claimed (a provider's own webhook, a
  dashboard refund): spread over the headroom left when it was booked;
* a staff decision that a lost dispute's goods came back: the chargeback
  spread over the headroom at the decision, capped to it.

The credit document is issued from these rows only, so the order credits are
issued in cannot change them (``order.credit_documents``), and the headroom
a new claim must fit in is a plain sum (``order.credit_allocation``).
"""

from __future__ import annotations

from django.db import models
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from djmoney.models.fields import MoneyField

from order.taxation import ChargeKind


class CreditAllocationQuerySet(models.QuerySet["CreditAllocation"]):
    def counting(self):
        """Rows that hold room on the receipt: those of a source that was
        not declined, and of a decision that the goods came back."""
        from order.models.refund_attempt import RefundAttemptStatus

        return (
            self.filter(amount__gt=0)
            .exclude(refund_attempt__status=RefundAttemptStatus.DECLINED)
            .exclude(decision__goods_returned=False)
        )


class CreditAllocation(models.Model):
    """One VAT rate's share of one credit source, gross.

    Exactly one of ``refund_attempt``, ``refund`` and ``decision`` is set
    (a database constraint). ``item`` and ``quantity`` are set on the pieces
    of a refund by lines. ``moved_at`` is when the money moved (the refund
    was booked, the value returned, the decision made); empty while the
    claim is in flight, when it counts as moving now. A receipt already
    nets what moved before it was created, so only rows that move at or
    after it hold room on it.

    The contract, exactly: a source with NO row is not decided yet, and
    owes no credit until it is (a source booked while a claim still PENDING
    holds the room; every write path and the periodic task decide it, oldest
    first, once that claim resolves). A source with a row of amount ZERO was
    decided and owes nothing: the part of a refund that gives a duplicate
    payment back, a share fully reversed, an order with nothing taxable, or
    a source the receipt has no room for because settled sources and issued
    credits took it (which the order's history says once). A zero row holds
    no room (:meth:`CreditAllocationQuerySet.counting`).
    """

    id = models.BigAutoField(primary_key=True)
    order = models.ForeignKey(
        "order.Order",
        related_name="credit_allocations",
        on_delete=models.CASCADE,
        verbose_name=_("Order"),
    )
    refund_attempt = models.ForeignKey(
        "order.RefundAttempt",
        related_name="allocations",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        verbose_name=_("Refund attempt"),
    )
    refund = models.ForeignKey(
        "order.PaymentTransaction",
        related_name="allocations",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        verbose_name=_("Refund"),
        help_text=_(
            "A booked refund no attempt claimed (the provider's webhook)."
        ),
    )
    decision = models.ForeignKey(
        "order.ChargebackDecision",
        related_name="allocations",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        verbose_name=_("Chargeback decision"),
    )
    kind = models.CharField(
        _("Kind"),
        max_length=16,
        choices=ChargeKind.choices,
        blank=True,
        default="",
        db_default="",
        help_text=_("Goods or shipping for a refund by lines; empty else."),
    )
    item = models.ForeignKey(
        "order.OrderItem",
        related_name="credit_allocations",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        verbose_name=_("Order line"),
    )
    rate = models.DecimalField(_("VAT rate"), max_digits=5, decimal_places=2)
    quantity = models.PositiveIntegerField(
        _("Quantity"),
        default=0,
        db_default=0,
        help_text=_("Units a refund by lines asked for; informational."),
    )
    amount = MoneyField(
        _("Amount"),
        max_digits=11,
        decimal_places=2,
        help_text=_("Gross, VAT included."),
    )
    moved_at = models.DateTimeField(
        _("Moved at"),
        null=True,
        blank=True,
        help_text=_("When the money moved; empty while it is in flight."),
    )
    uncovered_acknowledged_at = models.DateTimeField(
        _("Uncovered acknowledged at"),
        null=True,
        blank=True,
        help_text=_(
            "When staff acknowledged the uncovered part; it is then left "
            "out of the store dashboard alert."
        ),
    )
    uncovered = models.DecimalField(
        _("Uncovered"),
        max_digits=11,
        decimal_places=2,
        default=0,
        db_default=0,
        help_text=_(
            "The part of the source's money that moved but that the receipt "
            "had no room to credit; set on one row of the source."
        ),
    )

    understated = models.DecimalField(
        _("Understated"),
        max_digits=11,
        decimal_places=2,
        default=0,
        db_default=0,
        help_text=_(
            "Goods the receipt is short of: a refund it was issued net of "
            "was reported failed by the provider afterwards; set on one row "
            "of the source."
        ),
    )

    objects = CreditAllocationQuerySet.as_manager()

    class Meta(TypedModelMeta):
        verbose_name = _("Credit allocation")
        verbose_name_plural = _("Credit allocations")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gte=0),
                name="credit_allocation_amount_not_negative",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        refund_attempt__isnull=False,
                        refund__isnull=True,
                        decision__isnull=True,
                    )
                    | models.Q(
                        refund_attempt__isnull=True,
                        refund__isnull=False,
                        decision__isnull=True,
                    )
                    | models.Q(
                        refund_attempt__isnull=True,
                        refund__isnull=True,
                        decision__isnull=False,
                    )
                ),
                name="credit_allocation_exactly_one_source",
            ),
        ]
        indexes = [
            models.Index(
                fields=["order", "moved_at"], name="credit_alloc_order_ix"
            ),
        ]

    def __str__(self) -> str:
        return gettext("%(amount)s at %(rate)s%% VAT") % {
            "amount": self.amount,
            "rate": self.rate,
        }
