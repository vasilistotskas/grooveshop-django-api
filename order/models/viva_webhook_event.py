from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta

from core.models import TimeStampMixinModel


class VivaWebhookEvent(TimeStampMixinModel):
    """Database-level idempotency record for Viva Wallet webhook deliveries.

    Mirrors ``shipping_boxnow.BoxNowParcelEvent`` for Viva. The unique
    constraint on ``(transaction_id, event_type_id)`` prevents replay
    even if ``Order.metadata`` is cleared by an admin or a data
    migration. Without this, the only idempotency guard for payment
    webhooks was the ``viva_webhook_<txn>_<evt>`` key inside
    ``order.metadata`` — wiping that bag would reopen the door to
    duplicate ``mark_as_paid`` dispatches on Viva retries.

    No sensitive payload fields are stored here on purpose
    (GDPR Art. 5(1)(c) data minimisation): we keep only what is
    needed to identify the event and audit the dispatch outcome.
    """

    EVENT_TYPE_CHOICES = (
        (1796, _("Transaction Payment Created")),
        (1797, _("Transaction Reversal Created")),
        (1798, _("Transaction Failed")),
    )

    OUTCOME_PROCESSED = "processed"
    OUTCOME_SKIPPED = "skipped"
    OUTCOME_FAILED = "failed"
    OUTCOME_CHOICES = (
        (OUTCOME_PROCESSED, _("Processed")),
        (OUTCOME_SKIPPED, _("Skipped")),
        (OUTCOME_FAILED, _("Failed")),
    )

    transaction_id = models.CharField(
        _("Transaction ID"),
        max_length=64,
        help_text=_("Viva ``EventData.TransactionId`` — idempotency key"),
    )
    event_type_id = models.PositiveSmallIntegerField(
        _("Event Type ID"),
        choices=EVENT_TYPE_CHOICES,
        help_text=_("Viva ``EventTypeId`` — 1796/1797/1798"),
    )
    order = models.ForeignKey(
        "order.Order",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="viva_webhook_events",
        verbose_name=_("Order"),
    )
    order_code = models.CharField(
        _("Order Code"),
        max_length=64,
        blank=True,
        default="",
        help_text=_("Viva ``EventData.OrderCode`` — kept for audit"),
    )
    status_id = models.CharField(
        _("Status ID"),
        max_length=4,
        blank=True,
        default="",
        help_text=_("Viva ``EventData.StatusId`` ('F' = Finished)"),
    )
    outcome = models.CharField(
        _("Outcome"),
        max_length=16,
        choices=OUTCOME_CHOICES,
        default=OUTCOME_PROCESSED,
    )
    received_at = models.DateTimeField(
        _("Received At"),
        default=timezone.now,
    )

    class Meta(TypedModelMeta):
        verbose_name = _("Viva Webhook Event")
        verbose_name_plural = _("Viva Webhook Events")
        ordering = ["-received_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["transaction_id", "event_type_id"],
                name="viva_webhook_event_unique",
            ),
        ]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            BTreeIndex(
                fields=["received_at"],
                name="viva_webhook_received_ix",
            ),
            BTreeIndex(
                fields=["order"],
                name="viva_webhook_order_ix",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.transaction_id} #{self.event_type_id} ({self.outcome})"
