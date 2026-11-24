"""The demo shipment of a BOX NOW order: the parcel and the webhook events
an order in that state has (``BoxNowCarrier.create_demo_shipment``; the
rest is ``shipping.demo``)."""

from __future__ import annotations

from hashlib import sha1
from typing import TYPE_CHECKING

from django.db.models import F

from shipping.demo import FINAL_STATES, ShipmentRow, event_times
from shipping_boxnow.enum.compartment_size import BoxNowCompartmentSize
from shipping_boxnow.enum.payment_mode import BoxNowPaymentMode
from shipping_boxnow.models import BoxNowParcelEvent, BoxNowShipment

if TYPE_CHECKING:
    from datetime import datetime

    from djmoney.money import Money

    from order.models.order import Order

# BoxNow: (event type, parcelState on the wire, where, note).
_BOXNOW_STAGES: tuple[tuple[str, str, str], ...] = (
    ("new", "new", "Καταχωρήθηκε από το κατάστημα"),
    ("in_depot", "in-depot", "Στο κέντρο διαλογής BOX NOW"),
    ("final_destination", "final-destination", "Στο locker προορισμού"),
    ("delivered", "delivered", "Παραλήφθηκε από τον παραλήπτη"),
)
_BOXNOW_STAGES_BY_STATE: dict[str, int] = {
    "new": 1,
    "in_depot": 2,
    "final_destination": 3,
    "delivered": 4,
}
_BOXNOW_RETURNED = ("returned", "returned", "Επιστράφηκε στο κατάστημα")
_BOXNOW_RETURNED_AFTER = 3


def _boxnow_events(row: ShipmentRow) -> list[tuple[str, str, str]]:
    if row.state == "returned":
        return [*_BOXNOW_STAGES[:_BOXNOW_RETURNED_AFTER], _BOXNOW_RETURNED]
    return list(_BOXNOW_STAGES[: _BOXNOW_STAGES_BY_STATE[row.state]])


def create_demo_shipment(
    order: Order,
    row: ShipmentRow,
    *,
    placed_at: datetime,
    now: datetime,
    collect: Money,
    weight_grams: int,
) -> BoxNowShipment:
    """The BoxNow parcel and webhook events an order in ``row.state`` has.

    A locker is named by its BoxNow id only: no ``BoxNowLocker`` row is
    written, because that table is the cache the checkout's locker map
    reads and a demo must not put an invented locker on it.
    """
    events = _boxnow_events(row)
    times = event_times(
        placed_at,
        len(events),
        now,
        in_flight=row.state not in FINAL_STATES,
    )
    collects = collect.amount > 0
    shipment = BoxNowShipment.objects.create(
        order=order,
        delivery_request_id=f"demo-request-{row.voucher}",
        parcel_id=row.voucher,
        locker_external_id=row.locker_id,
        parcel_state=row.state,
        compartment_size=BoxNowCompartmentSize.SMALL,
        weight_grams=weight_grams,
        payment_mode=(
            BoxNowPaymentMode.COD if collects else BoxNowPaymentMode.PREPAID
        ),
        amount_to_be_collected=collect,
        last_polled_at=now,
        last_event_at=times[-1],
        metadata={
            "demo_seed": True,
            "locker": {
                "id": row.locker_id,
                "title": row.destination,
                "country": row.destination_country,
            },
        },
    )
    for (event_type, wire_state, note), moment in zip(
        events, times, strict=True
    ):
        BoxNowParcelEvent.objects.create(
            shipment=shipment,
            webhook_message_id=f"demo-{row.voucher}-{event_type}",
            data_fingerprint=sha1(
                f"{row.voucher}|{event_type}".encode()
            ).hexdigest(),
            event_type=event_type,
            parcel_state=wire_state,
            event_time=moment,
            display_name=row.destination,
            additional_information=note,
            raw_payload={"demo_seed": True, "event": wire_state},
            received_at=moment,
        )
    BoxNowParcelEvent.objects.filter(shipment=shipment).update(
        created_at=F("event_time"), updated_at=F("event_time")
    )
    BoxNowShipment.objects.filter(pk=shipment.pk).update(
        created_at=placed_at, updated_at=times[-1]
    )
    return shipment
