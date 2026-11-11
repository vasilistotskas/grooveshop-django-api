"""Fixture shipments for the demo accounts' orders.

An order list is only half the story: the page a prospect remembers is
the tracking timeline under an order that is on its way. These write the
carrier rows an order really has — an ACS voucher and its tracking
events, a BoxNow parcel and its webhook events — straight to the ORM.

Nothing here talks to a carrier. The rows are what the carrier clients
WOULD have written after a successful voucher and a few polls, so the
admin and the storefront render them like any other, and the polling
tasks never pick them up: ``last_polled_at`` is set and every state a
fixture ends in is either terminal or fresh enough to be skipped.
Voucher numbers carry a ``DEMO`` prefix so they can never be mistaken
for (or collide with) a real one.

The models are written with ``create()``, not ``bulk_create``: both
shipments keep an audit history (``simple_history``), which only a real
save records. Neither shipment model, nor their event models, has a
``post_save`` receiver — the carrier side effects live in the services,
which these bypass — and ``tests/unit/devtools/test_demo_account.py``
pins that.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha1
from typing import Any

from django.db.models import F
from djmoney.money import Money

#: Where the parcels leave from, as the carrier's own checkpoint text.
ORIGIN = "Θεσσαλονίκη"
HUB = "Κέντρο Διαλογής Αθήνας"

#: A parcel's checkpoints are this far apart.
STEP = timedelta(hours=7)


@dataclass(frozen=True)
class ShipmentRow:
    carrier: str  # "acs" | "boxnow"
    #: ``AcsShipmentState`` / ``BoxNowParcelState`` value the fixture ends in.
    state: str
    #: Becomes the order's tracking number and the carrier's voucher/parcel.
    voucher: str
    #: Where it goes: a station name for ACS, a locker for BoxNow.
    destination: str = ""
    #: BoxNow locker id, kept denormalised on the shipment.
    locker_id: str = ""
    destination_country: str = "GR"


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


#: A parcel in one of these states is finished: the carriers' pollers and
#: the stale-shipment alert skip it.
FINAL_STATES = frozenset({"delivered", "returned"})

#: How long ago a parcel in flight last reported.
LAST_SEEN = timedelta(hours=3)


def _event_times(
    placed_at: datetime, count: int, now: datetime, *, in_flight: bool
) -> list[datetime]:
    """``count`` checkpoint times, oldest first.

    A finished parcel's run starts soon after the order and never
    reaches into the future. A parcel still on its way is anchored to
    NOW instead: ``check_stale_acs_shipments`` emails the merchant about
    any non-terminal shipment whose last event is older than
    ``ACS_STALE_SHIPMENT_DAYS``, and a fixture seeded days ago would
    trip it however recently it was rebuilt.
    """
    if in_flight:
        last = now - LAST_SEEN
        return [last - STEP * (count - 1 - index) for index in range(count)]
    return [
        min(
            placed_at + timedelta(hours=2) + STEP * index,
            now - timedelta(minutes=5 * (count - index)),
        )
        for index in range(count)
    ]


def _acs_events(row: ShipmentRow) -> list[tuple[str, str]]:
    if row.state == "returned":
        stages = list(_ACS_STAGES[:_ACS_RETURNED_AFTER]) + [_ACS_RETURNED]
    else:
        stages = list(_ACS_STAGES[: _ACS_STAGES_BY_STATE[row.state]])
    return [
        (action, where.format(destination=row.destination))
        for action, where in stages
    ]


def _boxnow_events(row: ShipmentRow) -> list[tuple[str, str, str]]:
    if row.state == "returned":
        return [*_BOXNOW_STAGES[:_BOXNOW_RETURNED_AFTER], _BOXNOW_RETURNED]
    return list(_BOXNOW_STAGES[: _BOXNOW_STAGES_BY_STATE[row.state]])


def create_acs_shipment(
    order,
    row: ShipmentRow,
    *,
    placed_at: datetime,
    now: datetime,
    cod_amount: Money,
    weight_grams: int,
    pickup_point: bool,
) -> Any:
    """The ACS voucher and tracking events an order in ``row.state`` has.

    The voucher is always Charge_Type COD — the commercial contract is
    COD-only — with the amount the courier collects at the door: the
    order total for a cash-on-delivery order, nothing for one already
    paid online (see ``AcsCarrier.create_shipment_row``).
    """
    from shipping.enum import ShippingKind
    from shipping_acs.enum.charge_type import AcsChargeType
    from shipping_acs.enum.cod_payment_way import AcsCodPaymentWay
    from shipping_acs.models import AcsShipment, AcsTrackingEvent

    events = _acs_events(row)
    times = _event_times(
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
        cod_amount=cod_amount,
        cod_payment_way=AcsCodPaymentWay.CASH,
        delivery_products="COD" if cod_amount.amount > 0 else "",
        delivery_kind=(
            ShippingKind.PICKUP_POINT
            if pickup_point
            else ShippingKind.HOME_DELIVERY
        ),
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


def create_boxnow_shipment(
    order,
    row: ShipmentRow,
    *,
    placed_at: datetime,
    now: datetime,
    amount_to_collect: Money,
    weight_grams: int,
) -> Any:
    """The BoxNow parcel and webhook events an order in ``row.state`` has.

    A locker is named by its BoxNow id only: no ``BoxNowLocker`` row is
    written, because that table is the cache the checkout's locker map
    reads and a demo must not put an invented locker on it.
    """
    from shipping_boxnow.enum.compartment_size import BoxNowCompartmentSize
    from shipping_boxnow.enum.payment_mode import BoxNowPaymentMode
    from shipping_boxnow.models import BoxNowParcelEvent, BoxNowShipment

    events = _boxnow_events(row)
    times = _event_times(
        placed_at,
        len(events),
        now,
        in_flight=row.state not in FINAL_STATES,
    )
    collects = amount_to_collect.amount > 0
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
        amount_to_be_collected=amount_to_collect,
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
