from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models import TimeStampMixinModel


class MetaCapiEventStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    SENT = "sent", _("Sent")
    FAILED = "failed", _("Failed")
    SKIPPED = "skipped", _("Skipped")


class MetaCapiEventLog(TimeStampMixinModel):
    """Audit trail for every Conversions API dispatch.

    One row per (event_name, event_id) attempt. Used to:
    * surface ``fbtrace_id`` to staff when Meta debugging is needed,
    * confirm idempotency (the same ``event_id`` is never re-sent),
    * show ops a clean failure timeline in the admin.

    Intentionally light: no PII (the user_data fields are SHA-256
    hashed before they leave the building anyway). The full event
    body lives in ``payload`` as JSON for replay debugging.
    """

    event_name = models.CharField(_("Event name"), max_length=64)
    event_id = models.CharField(
        _("Event ID"),
        max_length=128,
        unique=True,
        help_text=_(
            "UUID shared between the browser pixel and the server "
            "event so Meta can dedup the pair. Unique constraint is "
            "the second line of defence against double dispatch — "
            "the Celery task already short-circuits if a row with "
            "this event_id is already SENT."
        ),
    )
    order = models.ForeignKey(
        "order.Order",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="meta_capi_events",
    )
    user = models.ForeignKey(
        "user.UserAccount",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="meta_capi_events",
    )
    status = models.CharField(
        _("Status"),
        max_length=16,
        choices=MetaCapiEventStatus.choices,
        default=MetaCapiEventStatus.PENDING,
    )
    fbtrace_id = models.CharField(
        _("Meta fbtrace_id"),
        max_length=128,
        blank=True,
        default="",
        help_text=_(
            "Meta's request trace ID. Quote this when filing a "
            "support ticket so they can find the request server-side."
        ),
    )
    events_received = models.PositiveSmallIntegerField(
        _("Events received"),
        default=0,
        help_text=_(
            "Number of events Meta acknowledged. Should be 1; "
            "values >1 indicate batched sends, 0 indicates a "
            "validation reject we logged but Meta dropped."
        ),
    )
    error_message = models.TextField(_("Error message"), blank=True, default="")
    payload = models.JSONField(
        _("Payload"),
        default=dict,
        blank=True,
        help_text=_(
            "Sanitised request body (PII fields are hashed by the "
            "facebook_business SDK before serialisation). Stored "
            "for replay during incident debugging."
        ),
    )

    class Meta:
        verbose_name = _("Meta CAPI Event")
        verbose_name_plural = _("Meta CAPI Events")
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("event_name", "-created_at")),
            models.Index(fields=("status", "-created_at")),
        ]

    def __str__(self) -> str:  # pragma: no cover — repr only
        return f"{self.event_name} [{self.status}] {self.event_id}"
