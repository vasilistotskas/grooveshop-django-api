from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta

from core.models import TimeStampMixinModel
from shipping_boxnow.enum.parcel_state import BoxNowParcelState


class BoxNowParcelEvent(TimeStampMixinModel):
    """
    Immutable audit record for each BoxNow webhook delivery event.

    ``webhook_message_id`` maps to the CloudEvents ``id`` field and
    acts as the idempotency key — the unique constraint prevents
    double-processing duplicate deliveries from BoxNow's retry logic.

    ``event_type`` is derived from the webhook ``event`` field
    (e.g. ``in-depot``), mapped to our ``BoxNowParcelState`` enum.
    ``parcel_state`` is the separate ``data.parcelState`` vocabulary
    sent by BoxNow alongside the event; both are stored to preserve
    full fidelity of the webhook payload.

    ``raw_payload`` stores the full request body for debugging.
    """

    shipment = models.ForeignKey(
        "shipping_boxnow.BoxNowShipment",
        on_delete=models.CASCADE,
        related_name="events",
        verbose_name=_("Shipment"),
    )
    webhook_message_id = models.CharField(
        _("Webhook Message ID"),
        max_length=128,
        unique=True,
        db_index=True,
        help_text=_("CloudEvents 'id' field — idempotency key"),
    )
    event_type = models.CharField(
        _("Event Type"),
        max_length=30,
        choices=BoxNowParcelState.choices,
        help_text=_("BoxNow event mapped from the webhook 'event' field"),
    )
    parcel_state = models.CharField(
        _("Parcel State"),
        max_length=64,
        blank=True,
        default="",
        help_text=_("Raw 'data.parcelState' value from BoxNow webhook payload"),
    )
    event_time = models.DateTimeField(
        _("Event Time"),
        help_text=_("Timestamp from 'data.time' in the webhook payload"),
    )
    display_name = models.CharField(
        _("Display Name"),
        max_length=255,
        blank=True,
        default="",
        help_text=_("data.eventLocation.displayName"),
    )
    postal_code = models.CharField(
        _("Postal Code"),
        max_length=20,
        blank=True,
        default="",
        help_text=_("data.eventLocation.postalCode"),
    )
    additional_information = models.TextField(
        _("Additional Information"),
        blank=True,
        default="",
    )
    raw_payload = models.JSONField(
        _("Raw Payload"),
        help_text=_("Full webhook request body"),
    )
    received_at = models.DateTimeField(
        _("Received At"),
        default=timezone.now,
        help_text=_(
            "Timestamp when GrooveShop received the webhook "
            "(separate from event_time)"
        ),
    )

    class Meta(TypedModelMeta):
        verbose_name = _("BoxNow Parcel Event")
        verbose_name_plural = _("BoxNow Parcel Events")
        ordering = ["-event_time"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            BTreeIndex(
                fields=["event_time"],
                name="boxnow_event_time_ix",
            ),
            BTreeIndex(
                fields=["event_type"],
                name="boxnow_event_type_ix",
            ),
            BTreeIndex(
                fields=["shipment"],
                name="boxnow_event_shipment_ix",
            ),
        ]

    def __str__(self) -> str:
        parcel_id = self.shipment.parcel_id if self.shipment_id else "unknown"
        return f"{parcel_id} → {self.event_type} @ {self.event_time}"
