"""Delivery-date arithmetic: dispatch cutoff, business days, Greek holidays.

A parcel is *dispatched* on the first business day the store can hand it
to the carrier: the placement day when the order lands before
``DISPATCH_CUTOFF`` on a business day, otherwise the next business day.
It then travels ``ShippingRate.delivery_days_max`` business days.

"Business day" means Monday to Friday excluding Greek public holidays —
the carriers this platform integrates with (ACS, BOX NOW) run on that
calendar. Everything is evaluated in the store's local time
(``settings.TIME_ZONE``), so an order placed at 23:30 UTC in summer is
already "tomorrow" in Athens.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from functools import lru_cache
from typing import TYPE_CHECKING

from django.utils import timezone

if TYPE_CHECKING:
    from shipping.models import ShippingRate


def orthodox_easter(year: int) -> date:
    """Orthodox Easter Sunday (Gregorian date), Meeus' Julian algorithm.

    The +13 day Julian-to-Gregorian offset holds for 1900-2099.
    """
    a, b, c = year % 4, year % 7, year % 19
    d = (19 * c + 15) % 30
    e = (2 * a + 4 * b - d + 34) % 7
    month, day = divmod(d + e + 114, 31)
    return date(year, month, day + 1) + timedelta(days=13)


@lru_cache(maxsize=32)
def greek_holidays(year: int) -> frozenset[date]:
    """Public holidays on which the carriers do not dispatch or deliver."""
    easter = orthodox_easter(year)
    fixed = [
        (1, 1),  # New Year
        (1, 6),  # Epiphany
        (3, 25),  # Independence Day
        (5, 1),  # Labour Day
        (8, 15),  # Dormition
        (10, 28),  # Ochi Day
        (12, 25),
        (12, 26),
    ]
    movable = [
        easter - timedelta(days=48),  # Clean Monday
        easter - timedelta(days=2),  # Good Friday
        easter + timedelta(days=1),  # Easter Monday
        easter + timedelta(days=50),  # Whit Monday
    ]
    return frozenset(date(year, m, d) for m, d in fixed) | frozenset(movable)


def is_business_day(day: date) -> bool:
    return day.weekday() < 5 and day not in greek_holidays(day.year)


def next_business_day(day: date) -> date:
    """``day`` itself when it is a business day, else the next one."""
    while not is_business_day(day):
        day += timedelta(days=1)
    return day


def add_business_days(day: date, days: int) -> date:
    """``day`` plus ``days`` business days (``day`` need not be one)."""
    while days > 0:
        day += timedelta(days=1)
        if is_business_day(day):
            days -= 1
    return day


def parse_cutoff(raw: object) -> time | None:
    """``DISPATCH_CUTOFF`` (``HH:MM``) as a time; empty means no cutoff."""
    if not raw:
        return None
    return time.fromisoformat(str(raw))


def dispatch_date(placed_at: datetime, cutoff: time | None) -> date:
    local = timezone.localtime(placed_at)
    day = local.date()
    if cutoff is not None and local.time() >= cutoff:
        day += timedelta(days=1)
    return next_business_day(day)


def estimated_delivery(
    rate: ShippingRate | None,
    placed_at: datetime,
    cutoff: time | None,
) -> date | None:
    """Latest expected delivery date, or ``None`` when the rate has no
    estimate. No default is invented for a rate that does not state one."""
    if rate is None or rate.delivery_days_max is None:
        return None
    return add_business_days(
        dispatch_date(placed_at, cutoff), rate.delivery_days_max
    )


def configured_cutoff() -> time | None:
    from extra_settings.models import Setting

    return parse_cutoff(Setting.get("DISPATCH_CUTOFF", default="15:00"))
