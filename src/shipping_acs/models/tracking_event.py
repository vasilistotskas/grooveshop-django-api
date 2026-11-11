from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta

from core.models import TimeStampMixinModel


class AcsTrackingEvent(TimeStampMixinModel):
    """Immutable poll-derived audit record.

    ACS does not have webhooks; the polling task fetches
    ``ACS_TrackingDetails`` for each non-terminal shipment and upserts
    rows here.

    Idempotency uses ``event_fingerprint`` (a SHA-1 of
    shipment_id + event_time + checkpoint_action + checkpoint_location)
    instead of BoxNow's ``webhook_message_id`` because ACS does not
    issue stable per-event IDs.
    """

    shipment = models.ForeignKey(
        "shipping_acs.AcsShipment",
        on_delete=models.CASCADE,
        related_name="events",
        verbose_name=_("Shipment"),
    )
    event_time = models.DateTimeField(
        _("Event time"),
        help_text=_("Checkpoint_Date_Time from ACS_TrackingDetails."),
    )
    checkpoint_action = models.CharField(
        _("Checkpoint action"),
        max_length=255,
        help_text=_(
            "Checkpoint_Action_Description — human-readable Greek "
            "text describing the event."
        ),
    )
    checkpoint_location = models.CharField(
        _("Checkpoint location"),
        max_length=255,
        blank=True,
        default="",
        help_text=_("Checkpoint_Location_Description."),
    )
    notes = models.TextField(
        _("Notes"),
        blank=True,
        default="",
        help_text=_("ACS Comments field."),
    )
    event_fingerprint = models.CharField(
        _("Event fingerprint"),
        max_length=64,
        unique=True,
        help_text=_(
            "SHA-1 of shipment_id|event_time|action|location — replaces "
            "BoxNow's webhook_message_id since ACS has no event ID."
        ),
    )
    raw_payload = models.JSONField(
        _("Raw payload"),
        help_text=_("Full row from ACS_TrackingDetails Table_Data."),
    )
    received_at = models.DateTimeField(
        _("Received at"),
        default=timezone.now,
        help_text=_(
            "Wall-clock time when the polling task observed this event."
        ),
    )

    class Meta(TypedModelMeta):
        verbose_name = _("ACS tracking event")
        verbose_name_plural = _("ACS tracking events")
        ordering = ["-event_time"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            BTreeIndex(fields=["shipment"], name="acs_event_shipment_ix"),
            BTreeIndex(fields=["event_time"], name="acs_event_time_ix"),
            BTreeIndex(
                fields=["event_fingerprint"], name="acs_event_fingerprint_ix"
            ),
        ]

    def __str__(self) -> str:
        voucher = self.shipment.voucher_no if self.shipment_id else "?"
        return f"{voucher} → {self.checkpoint_action} @ {self.event_time}"
