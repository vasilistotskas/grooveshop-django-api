"""Internal shipment state derived from ACS_Trackingsummary fields.

ACS does not have webhooks; we poll ``ACS_Trackingsummary`` and map the
numeric ``delivery_flag`` / ``returned_flag`` / ``shipment_status``
triplet to a stable enum.

Source for the mapping: ACS REST API PDF section "TRACKING (Summary)"
— specifically the textual description of ``shipment_status`` codes
and the worked examples in that section.
"""

from __future__ import annotations

from typing import Any

from django.db import models
from django.utils.translation import gettext_lazy as _


class AcsShipmentState(models.TextChoices):
    PENDING_CREATION = "pending_creation", _("Pending creation")
    NEW = "new", _("New")
    IN_TRANSIT = "in_transit", _("In transit")
    AT_DESTINATION = "at_destination", _("At destination station")
    OUT_FOR_DELIVERY = "out_for_delivery", _("Out for delivery")
    DELIVERED = "delivered", _("Delivered")
    ATTEMPTED = "attempted", _("Delivery attempted")
    RETURNED = "returned", _("Returned")
    CANCELED = "canceled", _("Canceled")
    LOST = "lost", _("Lost")

    @classmethod
    def from_tracking_summary(
        cls, payload: dict[str, Any], *, current: "AcsShipmentState"
    ) -> "AcsShipmentState":
        """Map a single ``ACS_Trackingsummary`` row to the internal enum.

        Inputs (per ACS PDF):
        * ``delivery_flag``  — 1 = delivered, 0 = not delivered.
        * ``returned_flag``  — 1 = returned to sender.
        * ``shipment_status`` — 1 unloaded, 2 loaded, 3 delivery attempted,
          4 on courier vehicle, 5 delivered.

        Falls back to ``current`` when the payload is missing/garbled
        rather than letting a single bad poll downgrade an already-
        terminal shipment.
        """
        delivery = _normalise_flag(payload.get("delivery_flag"))
        returned = _normalise_flag(payload.get("returned_flag"))
        status_raw = payload.get("shipment_status")

        if returned == 1:
            return cls.RETURNED
        if delivery == 1:
            return cls.DELIVERED

        try:
            status = int(status_raw) if status_raw not in (None, "") else None
        except (TypeError, ValueError):
            status = None

        mapping: dict[int, AcsShipmentState] = {
            1: cls.NEW,
            2: cls.IN_TRANSIT,
            3: cls.AT_DESTINATION,
            4: cls.OUT_FOR_DELIVERY,
            5: cls.ATTEMPTED,  # delivery_flag handled above; status=5
            # alone implies attempted but not delivered
        }
        if status in mapping:
            return mapping[status]
        return current


def _normalise_flag(value: Any) -> int | None:
    """Coerce ACS's '0'/'1'/0/1/None flag values to int or None."""
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
