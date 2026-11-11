"""One shipment's story, in time order, for the admin diagnostics panel.

Reads only: the shipment's own columns, the pickup-list runs that had its
voucher among their candidates, its tracking events and the ACS calls that
named its voucher. Request and response payloads are NOT shown here (they
carry recipient details); the superuser-only ``AcsApiCall`` admin has them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.utils.translation import gettext as _

from shipping_acs.models import (
    AcsApiCall,
    AcsPickupListRun,
    AcsShipment,
)

# The newest ACS calls shown for one voucher.
LAST_CALLS = 10


@dataclass(frozen=True)
class TimelineEntry:
    at: datetime
    kind: str
    text: str


def shipment_timeline(shipment: AcsShipment) -> list[TimelineEntry]:
    """Oldest-first events for ``shipment``."""
    entries = [
        TimelineEntry(
            shipment.created_at,
            "shipment",
            _("Shipment row created"),
        )
    ]
    if shipment.label_printed_at:
        entries.append(
            TimelineEntry(
                shipment.label_printed_at,
                "label",
                _("Label printed (voucher %(voucher)s)")
                % {"voucher": shipment.voucher_no},
            )
        )

    voucher = shipment.voucher_no
    if voucher:
        runs = AcsPickupListRun.objects.filter(
            candidate_vouchers__contains=[voucher]
        ).order_by("started_at")
        for run in runs:
            named = voucher in (run.unprinted_vouchers_acs or [])
            local = voucher in (run.unprinted_vouchers_local or [])
            detail = run.get_outcome_display()
            if named:
                detail += " — " + _("ACS named this voucher as unprinted")
            elif local:
                detail += " — " + _("unprinted locally")
            entries.append(
                TimelineEntry(
                    run.started_at,
                    "run",
                    _("Pickup list run for %(date)s: %(detail)s")
                    % {"date": run.pickup_date, "detail": detail},
                )
            )
        calls = list(
            AcsApiCall.objects.filter(voucher_no=voucher).order_by(
                "-created_at", "-id"
            )[:LAST_CALLS]
        )
        for call in reversed(calls):
            suffix = f": {call.error_message}" if call.error_message else ""
            entries.append(
                TimelineEntry(
                    call.created_at,
                    "call",
                    _("ACS call %(alias)s: %(outcome)s (%(ms)s ms)")
                    % {
                        "alias": call.alias,
                        "outcome": call.get_outcome_display(),
                        "ms": call.duration_ms,
                    }
                    + suffix,
                )
            )

    if shipment.pickup_list_id:
        pickup_list = shipment.pickup_list
        entries.append(
            TimelineEntry(
                pickup_list.issued_at,
                "pickup_list",
                _("On pickup list %(no)s") % {"no": pickup_list.pickup_list_no},
            )
        )
    entries.extend(
        TimelineEntry(
            event.event_time,
            "tracking",
            f"{event.checkpoint_action} ({event.checkpoint_location})"
            if event.checkpoint_location
            else event.checkpoint_action,
        )
        for event in shipment.events.all()
    )
    return sorted(entries, key=lambda entry: entry.at)
