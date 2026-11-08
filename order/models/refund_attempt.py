from __future__ import annotations

import uuid
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from djmoney.models.fields import MoneyField

#: What the provider's idempotency key of an attempt starts with
#: (``RefundAttempt.provider_key``); the rest is the attempt's UUID.
PROVIDER_KEY_PREFIX = "refund-"


def reconcile_after() -> timedelta:
    """How long a refund attempt may stay PENDING before the provider is
    asked what became of it (``REFUND_RECONCILE_AFTER_MINUTES``). Until
    then a retry may still reuse the attempt's idempotency key: it stays
    well inside the 24 hours Stripe remembers a key for
    (docs.stripe.com/api/idempotent_requests). It bounds only when the
    answer is looked for; it never frees the money an attempt holds."""
    return timedelta(minutes=settings.REFUND_RECONCILE_AFTER_MINUTES)


class RefundAttemptStatus(models.TextChoices):
    PENDING = "PENDING", _("Pending")
    SETTLED = "SETTLED", _("Settled")
    DECLINED = "DECLINED", _("Declined")


class RefundAttemptKind(models.TextChoices):
    PROVIDER = "PROVIDER", _("Payment provider")
    STORED_VALUE = "STORED_VALUE", _("Gift cards and points")


class RefundAttemptQuerySet(models.QuerySet["RefundAttempt"]):
    def live(self):
        """Attempts that hold their amount back: every one still PENDING,
        whatever its age. Only a settled or declined attempt lets go of
        its money; time never does, because a refund the provider made
        late would otherwise be refundable a second time."""
        return self.filter(status=RefundAttemptStatus.PENDING)

    def with_provider_key(self, key: str):
        """The attempt a provider key (:attr:`RefundAttempt.provider_key`)
        names, found by its UUID: none for a key that is not one of ours."""
        try:
            token = uuid.UUID(key.removeprefix(PROVIDER_KEY_PREFIX))
        except ValueError:
            return self.none()
        if not key.startswith(PROVIDER_KEY_PREFIX):
            return self.none()
        return self.filter(idempotency_key=token)

    def overdue(self):
        """Provider attempts still PENDING after :func:`reconcile_after`:
        the ones whose outcome is looked up (``order.tasks``) and, when
        that cannot decide, recorded by staff. A stored-value attempt is
        never overdue: no provider is involved, a retry of the refund
        finishes it."""
        return self.live().filter(
            kind=RefundAttemptKind.PROVIDER,
            created_at__lte=timezone.now() - reconcile_after(),
        )

    def awaiting_staff(self):
        """Overdue attempts the automatic lookup asked the provider about
        and could not decide (``looked_up_at``): what staff are told about
        and may record the outcome of. Until the lookup has had its turn
        the provider may still answer on its own."""
        return self.overdue().filter(looked_up_at__isnull=False)

    def stalled(self):
        """Gift card and points returns still PENDING after
        :func:`reconcile_after`: a crash left them unfinished. No provider
        can answer for them and staff have no outcome to record: repeating
        the refund of the same amount resumes and finishes the attempt
        (``OrderService._attempts_to_resume``). Until then it holds its
        money, defers the order's receipt and refuses a new refund."""
        return self.live().filter(
            kind=RefundAttemptKind.STORED_VALUE,
            created_at__lte=timezone.now() - reconcile_after(),
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

    A crash between the call and the booking leaves the attempt PENDING,
    and it keeps holding its amount until something resolves it: the
    provider's webhook books the refund (and settles the attempt it
    matches, whatever its age), a retry reuses the key while the provider
    still remembers it, the reconciliation task asks the provider once the
    attempt is overdue, or staff record the outcome. Money facts stay in
    the ledger: this row is only the claim.

    A ``STORED_VALUE`` attempt claims the part of a refund that goes back
    onto gift cards and points rather than through a provider: it names no
    charge, calls nobody, and is executed in one transaction after every
    provider share was made. What it returned is in the gift-card and
    points ledgers, whose rows name the attempt.
    """

    id = models.BigAutoField(primary_key=True)
    order = models.ForeignKey(
        "order.Order",
        related_name="refund_attempts",
        on_delete=models.PROTECT,
        verbose_name=_("Order"),
    )
    kind = models.CharField(
        _("Kind"),
        max_length=16,
        choices=RefundAttemptKind.choices,
        default=RefundAttemptKind.PROVIDER,
        db_default=RefundAttemptKind.PROVIDER,
        help_text=_(
            "A share paid back through a payment provider, or the value "
            "of what the order paid with gift cards and points."
        ),
    )
    charge = models.ForeignKey(
        "order.PaymentTransaction",
        related_name="refund_attempts",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        verbose_name=_("Charge"),
        help_text=_(
            "The payment this share is refunded against; none for gift "
            "cards and points."
        ),
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
    looked_up_at = models.DateTimeField(
        _("Looked up at"),
        null=True,
        blank=True,
        help_text=_(
            "When the automatic lookup last asked the payment provider "
            "about this refund and could not decide it."
        ),
    )

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

    @property
    def provider_key(self) -> str:
        """The idempotency key the provider receives for this attempt (and
        that a Stripe refund carries in its metadata)."""
        return f"{PROVIDER_KEY_PREFIX}{self.idempotency_key}"

    @property
    def is_overdue(self) -> bool:
        """Whether this is a provider attempt still PENDING past
        :func:`reconcile_after` (:meth:`RefundAttemptQuerySet.overdue`)."""
        return (
            self.status == RefundAttemptStatus.PENDING
            and self.kind == RefundAttemptKind.PROVIDER
            and self.created_at <= timezone.now() - reconcile_after()
        )

    @property
    def is_awaiting_staff(self) -> bool:
        """Whether staff may record this attempt's outcome
        (:meth:`RefundAttemptQuerySet.awaiting_staff`)."""
        return self.is_overdue and self.looked_up_at is not None
