"""Demo shipments: the parcel a demo account's order shipped under.

An order list is only half the story: the page a prospect remembers is
the tracking timeline under an order that is on its way. The demo seed
(``devtools.demo_account``) names each order's parcel with a
:class:`ShipmentRow`, and the order's carrier writes the rows it really
has for it (``ShippingCarrierInterface.create_demo_shipment``: an ACS
voucher and its tracking events, a BOX NOW parcel and its webhook events)
straight to the ORM, timed by :func:`event_times`.

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

from shipping.enum import ShippingKind

#: Where the parcels leave from, as the carrier's own checkpoint text.
ORIGIN = "Θεσσαλονίκη"
HUB = "Κέντρο Διαλογής Αθήνας"

#: A parcel's checkpoints are this far apart.
STEP = timedelta(hours=7)


@dataclass(frozen=True)
class ShipmentRow:
    #: The carrier's ``code``.
    carrier: str
    #: The carrier's own state value the fixture ends in.
    state: str
    #: Becomes the order's tracking number and the carrier's voucher/parcel.
    voucher: str
    #: Where it goes: a station name for ACS, a locker for BoxNow.
    destination: str = ""
    #: BoxNow locker id, kept denormalised on the shipment.
    locker_id: str = ""
    destination_country: str = "GR"
    #: How the order is delivered: to the door, or to a pickup point.
    kind: ShippingKind = ShippingKind.HOME_DELIVERY


#: A parcel in one of these states is finished: the carriers' pollers and
#: the stale-shipment alert skip it.
FINAL_STATES = frozenset({"delivered", "returned"})

#: How long ago a parcel in flight last reported.
LAST_SEEN = timedelta(hours=3)


def event_times(
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
