"""The "follow up" flag: an order the shopper has waited on for N days.

The store owner's purpose is customer service. A shopper who ordered N
calendar days ago and has no parcel is about to write in; counting from
placement catches both an order nobody shipped and a parcel stuck in
transit, and a reason says which.

The rule is one queryset (``OrderQuerySet.needing_follow_up``); the
admin badge, its filter and the dashboard counter all read it, so they
cannot disagree. Nothing is stored: the flag disappears by itself when the
order is delivered, completed, returned, canceled or refunded.

An order needs follow-up when ALL of these hold:

* it was placed on a local day at least ``ORDER_FOLLOW_UP_DAYS`` days ago
  (store timezone, whole calendar days: an order placed on day D is
  flagged from 00:00 on day D+N); ``0`` switches the flag off;
* its status is PENDING, PROCESSING or SHIPPED;
* it is not an online order still waiting for its payment: an unpaid card
  order is an abandoned checkout, not a parcel to chase.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING

from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from order.enum.status import OrderStatus
from order.managers.order import awaiting_online_payment_q

if TYPE_CHECKING:
    from django.utils.functional import _StrPromise

    from order.models.order import Order

FOLLOW_UP_DAYS_SETTING = "ORDER_FOLLOW_UP_DAYS"


def follow_up_days() -> int:
    """The store's threshold in calendar days; ``0`` means off."""
    from core.store_settings import store_setting

    return max(
        0,
        int(store_setting(FOLLOW_UP_DAYS_SETTING)),
    )


def follow_up_statuses() -> list[OrderStatus]:
    """Statuses in which a shopper is still waiting for the parcel."""
    groups = OrderStatus.get_status_groups()
    return [*groups["needs_processing"], *groups["in_transit"]]


def follow_up_cutoff(days: int, now: datetime | None = None) -> datetime | None:
    """Orders placed before this instant are ``days`` or more days old.

    The first instant of the local day ``days - 1`` days before today:
    an order placed on local day D is older than the cutoff exactly from
    day D+days. ``None`` when ``days`` is 0 (off).
    """
    if days <= 0:
        return None
    today = timezone.localdate(now)
    return timezone.make_aware(
        datetime.combine(today - timedelta(days=days - 1), time.min)
    )


def follow_up_q(now: datetime | None = None) -> Q | None:
    """The rule as a ``Q`` on ``Order``; ``None`` when the flag is off."""
    cutoff = follow_up_cutoff(follow_up_days(), now)
    if cutoff is None:
        return None
    return (
        Q(status__in=follow_up_statuses(), created_at__lt=cutoff)
        & ~awaiting_online_payment_q()
    )


def follow_up_reason(order: Order) -> _StrPromise | None:
    """Why the order is flagged: not shipped yet, or on its way."""
    groups = OrderStatus.get_status_groups()
    if order.status in groups["needs_processing"]:
        return _("Not shipped yet")
    if order.status in groups["in_transit"]:
        return _("In transit")
    return None


def last_carrier_event_at(order: Order) -> datetime | None:
    """When the carrier last reported on the parcel, ACS or BoxNow."""
    events = [
        shipment.last_event_at
        for shipment in (
            getattr(order, "acs_shipment", None),
            getattr(order, "boxnow_shipment", None),
        )
        if shipment is not None and shipment.last_event_at is not None
    ]
    return max(events, default=None)
