from __future__ import annotations

import uuid
from datetime import timedelta

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from djmoney.models.fields import MoneyField

#: How long an attempt that never settled keeps its amount reserved. It
#: bounds the window in which a retry may reuse the attempt's idempotency
#: key, and stays well inside the 24 hours Stripe remembers a key for
#: (docs.stripe.com/api/idempotent_requests). After it the amount is
#: refundable again; a refund the provider did make late is still booked by
#: its own webhook.
ATTEMPT_RESERVATION = timedelta(hours=1)


class RefundAttemptStatus(models.TextChoices):
    PENDING = "PENDING", _("Pending")
    SETTLED = "SETTLED", _("Settled")
    DECLINED = "DECLINED", _("Declined")


class RefundAttemptQuerySet(models.QuerySet["RefundAttempt"]):
    def live(self):
        """Attempts that still hold their amount back: not settled, not
        declined, and not abandoned (:data:`ATTEMPT_RESERVATION`)."""
        return self.filter(
            status=RefundAttemptStatus.PENDING,
            created_at__gte=timezone.now() - ATTEMPT_RESERVATION,
        )


class RefundAttempt(models.Model):
    """One share of a refund, claimed before the provider is called.

    ``OrderService.refund_order`` runs in three steps so that no database
    transaction is open while a provider answers (a connection idle in a
    transaction is killed after ten seconds): the attempt is claimed under
    the order lock and committed, the provider is called with no
    transaction, and the result is booked on the ledger under the lock
    again. While the attempt is PENDING its amount is reserved against its
    charge (``PaymentLedger.balances``), so a second refund cannot take the
    same money, and its ``idempotency_key`` is what the provider receives:
    a retry or a double click reuses the attempt and a provider that honours
    keys hands back the refund it already made instead of making another.

    A crash between the call and the booking leaves the attempt PENDING;
    the provider's webhook books the refund (and settles the attempt it
    matches) or a retry reuses the key. Money facts stay in the ledger:
    this row is only the claim.
    """

    id = models.BigAutoField(primary_key=True)
    order = models.ForeignKey(
        "order.Order",
        related_name="refund_attempts",
        on_delete=models.PROTECT,
        verbose_name=_("Order"),
    )
    charge = models.ForeignKey(
        "order.PaymentTransaction",
        related_name="refund_attempts",
        on_delete=models.CASCADE,
        verbose_name=_("Charge"),
        help_text=_("The payment this share is refunded against."),
    )
    amount = MoneyField(_("Amount"), max_digits=11, decimal_places=2)
    reason = models.CharField(
        _("Reason"), max_length=255, blank=True, default=""
    )
    idempotency_key = models.UUIDField(
        _("Idempotency key"),
        default=uuid.uuid4,
        unique=True,
        editable=False,
        help_text=_("Sent to the provider so a repeat is not a second refund."),
    )
    status = models.CharField(
        _("Status"),
        max_length=16,
        choices=RefundAttemptStatus.choices,
        default=RefundAttemptStatus.PENDING,
    )
    refund = models.OneToOneField(
        "order.PaymentTransaction",
        related_name="attempt",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        verbose_name=_("Refund"),
        help_text=_("The ledger refund this attempt produced."),
    )
    created_at = models.DateTimeField(_("Created At"), auto_now_add=True)

    objects = RefundAttemptQuerySet.as_manager()

    class Meta(TypedModelMeta):
        verbose_name = _("Refund Attempt")
        verbose_name_plural = _("Refund Attempts")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name="refund_attempt_amount_positive",
            ),
        ]
        indexes = [
            models.Index(
                fields=["charge", "status"], name="refund_attempt_charge_ix"
            ),
        ]

    def __str__(self) -> str:
        return gettext("Refund attempt %(key)s of %(amount)s") % {
            "key": self.idempotency_key,
            "amount": self.amount,
        }
