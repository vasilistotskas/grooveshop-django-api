"""The reminder to review what was delivered.

One email per order, ``REVIEW_REQUEST_DELAY_DAYS`` days after the order was
delivered (``order.review_request``, sent by
``order.tasks.send_review_request_emails``). The delivered and completed
emails already invite a review at the moment of delivery; this is the second
chance, once the customer has had the goods in their hands.

When an order was delivered is the time of its first move to ``DELIVERED``,
which ``OrderHistory`` records for every status change whatever path made it
(``order.signals.handlers.handle_order_status_changed``). ``Order`` has no
column for it: ``status_updated_at`` moves again when a delivered order
completes, days later for cash on delivery. An order with no such record (it
was created already delivered) is never reminded.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from django.conf import settings
from django.db.models import Exists, OuterRef
from django.utils import timezone

from core.store_settings import store_setting
from order.emails import REVIEW_INVITING_STATUSES
from order.enum.status import OrderStatus
from order.models.history import OrderHistory
from order.models.order import Order

if TYPE_CHECKING:
    from django.db.models import QuerySet

DELAY_SETTING = "REVIEW_REQUEST_DELAY_DAYS"

#: The longest delay a store can set: a reminder a year after the delivery
#: is not a reminder.
MAX_DELAY_DAYS = 365


def validate_review_request_delay_days_setting(value: object) -> bool:
    """django-extra-settings validator for ``REVIEW_REQUEST_DELAY_DAYS``:
    a whole number of days, ``0`` turning the reminder off."""
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= MAX_DELAY_DAYS
    )


def delivery_window(
    now: datetime | None = None,
) -> tuple[datetime, datetime] | None:
    """The deliveries a run reminds, as ``(after, until)``: those at least
    the store's delay old, and no older than the delay plus
    ``REVIEW_REQUEST_CATCH_UP_DAYS``. ``None`` while the reminder is off.

    The lower bound is what keeps a store that switches the reminder on
    (or raises the delay) from mailing every order it ever delivered."""
    delay = int(store_setting(DELAY_SETTING))
    if delay <= 0:
        return None
    until = (now or timezone.now()) - timedelta(days=delay)
    return until - timedelta(days=settings.REVIEW_REQUEST_CATCH_UP_DAYS), until


def awaiting_review_request(
    window: tuple[datetime, datetime],
) -> QuerySet[Order]:
    """Orders still delivered (or completed), not reminded yet, whose
    delivery fell in *window*."""
    after, until = window
    delivered = OrderHistory.objects.filter(
        order=OuterRef("pk"),
        change_type=OrderHistory.OrderHistoryChangeType.STATUS,
        new_value__status=OrderStatus.DELIVERED,
        created_at__gt=after,
        created_at__lte=until,
    )
    return Order.objects.filter(
        Exists(delivered),
        status__in=REVIEW_INVITING_STATUSES,
        review_request_sent_at__isnull=True,
    )
