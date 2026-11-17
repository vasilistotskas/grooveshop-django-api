"""The agent gateway's order events, kept until the gateway has them.

Read ``docs/agent-order-events.md``.
"""

from __future__ import annotations

import uuid

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta


class AgentOrderEventType(models.TextChoices):
    CREATED = "created", _("Order created")
    UPDATED = "updated", _("Order updated")


class AgentOrderEventStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    DELIVERED = "delivered", _("Delivered")
    FAILED = "failed", _("Failed")


class AgentOrderEvent(models.Model):
    """One event of an agent's order that the agent gateway must be told of.

    Written in the transaction of the change it records, so an event
    exists exactly when its change committed. Its id is the event's
    ``Webhook-Id`` and ``occurred_at`` its ``Webhook-Timestamp``: both
    are sent unchanged on every attempt, so a platform can tell a retry
    from a new event. ``agent.outbox`` delivers it.
    """

    id = models.UUIDField(
        _("Webhook ID"), primary_key=True, default=uuid.uuid7, editable=False
    )
    order = models.ForeignKey(
        "order.Order",
        on_delete=models.CASCADE,
        related_name="agent_events",
        verbose_name=_("Order"),
    )
    event_type = models.CharField(
        _("Event"), max_length=16, choices=AgentOrderEventType.choices
    )
    occurred_at = models.DateTimeField(_("Occurred at"), default=timezone.now)
    status = models.CharField(
        _("Status"),
        max_length=16,
        choices=AgentOrderEventStatus.choices,
        default=AgentOrderEventStatus.PENDING,
    )
    attempts = models.PositiveSmallIntegerField(_("Attempts"), default=0)
    next_attempt_at = models.DateTimeField(
        _("Next attempt at"), default=timezone.now
    )
    last_attempt_at = models.DateTimeField(
        _("Last attempt at"), null=True, blank=True
    )
    finished_at = models.DateTimeField(_("Finished at"), null=True, blank=True)
    last_error = models.CharField(
        _("Last error"), max_length=255, blank=True, default=""
    )

    class Meta(TypedModelMeta):
        verbose_name = _("Agent order event")
        verbose_name_plural = _("Agent order events")
        ordering = ("-occurred_at",)
        indexes = [
            # The sweep's query: pending rows whose attempt is due.
            models.Index(
                fields=("status", "next_attempt_at"), name="agent_event_due_ix"
            ),
            # The retention sweep's query: finished rows past their days.
            models.Index(
                fields=("status", "finished_at"),
                name="agent_event_finished_ix",
            ),
            models.Index(fields=("-occurred_at",), name="agent_event_time_ix"),
        ]

    def __str__(self) -> str:
        return f"{self.event_type} {self.order_id} [{self.status}] {self.pk}"
