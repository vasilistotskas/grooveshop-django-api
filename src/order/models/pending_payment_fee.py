from __future__ import annotations

from django.db import models
from django.db.models.functions import Lower
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from djmoney.models.fields import MoneyField

from order.enum.payment_transaction import PaymentFeeSource


class PendingPaymentFee(models.Model):
    """A fee a provider reported before the ledger held the movement it is
    for.

    Providers report a fee on their own schedule (Viva's "transaction price
    calculated" can arrive before the payment webhook that books the
    charge). Answering such a report with a success while dropping the figure
    would lose it for good, so it waits here, keyed by the provider's own id
    for the movement, until ``PaymentLedger`` records that row and moves the
    fee onto it. The row is then deleted: the fee lives on the ledger row.
    """

    id = models.BigAutoField(primary_key=True)
    provider = models.CharField(_("Provider"), max_length=32)
    provider_reference = models.CharField(
        _("Provider reference"),
        max_length=255,
        help_text=_("The provider's own id for the movement the fee is for."),
    )
    fee = MoneyField(_("Provider fee"), max_digits=11, decimal_places=2)
    fee_source = models.CharField(
        _("Fee source"), max_length=32, choices=PaymentFeeSource.choices
    )
    created_at = models.DateTimeField(_("Created At"), auto_now_add=True)

    class Meta(TypedModelMeta):
        verbose_name = _("Pending payment fee")
        verbose_name_plural = _("Pending payment fees")
        constraints = [
            # References are matched without regard to case, as the ledger
            # matches them when it writes a fee.
            models.UniqueConstraint(
                "provider",
                Lower("provider_reference"),
                name="unique_pending_payment_fee_reference",
            ),
            models.CheckConstraint(
                condition=models.Q(fee__gte=0),
                name="pending_payment_fee_not_negative",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.provider} {self.provider_reference}: {self.fee}"
