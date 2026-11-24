"""The demo shipment of an ACS order: the voucher and the tracking events
an order in that state has (``AcsCarrier.create_demo_shipment``; the rest
is ``shipping.demo``)."""

from __future__ import annotations

from hashlib import sha1
from typing import TYPE_CHECKING

from django.db.models import F

from shipping.demo import FINAL_STATES, HUB, ORIGIN, ShipmentRow, event_times
from shipping_acs.enum.charge_type import AcsChargeType
from shipping_acs.enum.cod_payment_way import AcsCodPaymentWay
from shipping_acs.models import AcsShipment, AcsTrackingEvent

if TYPE_CHECKING:
    from datetime import datetime

    from djmoney.money import Money

    from order.models.order import Order

# ACS: each stage is (checkpoint text, where). ``DESTINATION`` stands for
# the row's own destination.
_DESTINATION = "{destination}"
_ACS_STAGES: tuple[tuple[str, str], ...] = (
    ("Δημιουργία αποστολής", ORIGIN),
    ("Παραλαβή από τον αποστολέα", ORIGIN),
    ("Αναχώρηση από το κέντρο διαλογής", HUB),
    ("Άφιξη στο κατάστημα προορισμού", _DESTINATION),
    ("Σε διανομή με κούριερ", _DESTINATION),
    ("Παραδόθηκε", _DESTINATION),
)
_ACS_STAGES_BY_STATE: dict[str, int] = {
    "new": 1,
    "in_transit": 3,
    "out_for_delivery": 5,
    "delivered": 6,
}
_ACS_RETURNED = ("Επιστροφή στον αποστολέα", _DESTINATION)
_ACS_RETURNED_AFTER = 4


def _acs_events(row: ShipmentRow) -> list[tuple[str, str]]:
    if row.state == "returned":
        stages = list(_ACS_STAGES[:_ACS_RETURNED_AFTER]) + [_ACS_RETURNED]
    else:
        stages = list(_ACS_STAGES[: _ACS_STAGES_BY_STATE[row.state]])
    return [
        (action, where.format(destination=row.destination))
        for action, where in stages
    ]


def create_demo_shipment(
    order: Order,
    row: ShipmentRow,
    *,
    placed_at: datetime,
    now: datetime,
    collect: Money,
    weight_grams: int,
) -> AcsShipment:
    """The ACS voucher and tracking events an order in ``row.state`` has.

    The voucher is always Charge_Type COD — the commercial contract is
    COD-only — with the amount the courier collects at the door
    (*collect*): the order total for a cash-on-delivery order, nothing for
    one already paid online (see ``AcsCarrier.create_shipment_row``).
    """
    events = _acs_events(row)
    times = event_times(
        placed_at,
        len(events),
        now,
        in_flight=row.state not in FINAL_STATES,
    )
    delivered = row.state == "delivered"
    shipment = AcsShipment.objects.create(
        order=order,
        voucher_no=row.voucher,
        shipment_state=row.state,
        weight_grams=weight_grams,
        item_quantity=1,
        charge_type=AcsChargeType.COD,
        cod_amount=collect,
        cod_payment_way=AcsCodPaymentWay.CASH,
        delivery_products="COD" if collect.amount > 0 else "",
        delivery_kind=row.kind,
        label_printed_at=times[0],
        delivery_flag="1" if delivered else "0",
        returned_flag="1" if row.state == "returned" else "0",
        raw_shipment_status="5" if delivered else "4",
        delivery_date=times[-1] if delivered else None,
        last_polled_at=now,
        last_event_at=times[-1],
        metadata={"demo_seed": True},
    )
    for (action, where), moment in zip(events, times, strict=True):
        fingerprint = sha1(
            f"{shipment.pk}|{moment.isoformat()}|{action}|{where}".encode()
        ).hexdigest()
        AcsTrackingEvent.objects.create(
            shipment=shipment,
            event_time=moment,
            checkpoint_action=action,
            checkpoint_location=where,
            event_fingerprint=fingerprint,
            raw_payload={"demo_seed": True, "action": action, "where": where},
            received_at=moment,
        )
    # ``created_at`` is ``auto_now_add``: backdate to when it happened.
    AcsTrackingEvent.objects.filter(shipment=shipment).update(
        created_at=F("event_time"), updated_at=F("event_time")
    )
    AcsShipment.objects.filter(pk=shipment.pk).update(
        created_at=placed_at, updated_at=times[-1]
    )
    return shipment
