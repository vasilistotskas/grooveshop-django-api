"""Dispatch cutoff, business-day and Greek-holiday arithmetic.

Dates are chosen against the real calendar: 2026-10-05 is a Monday,
Orthodox Easter 2026 is 12 April, and Athens is UTC+3 in October (EEST),
UTC+2 in December (EET).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time

import pytest

from shipping.delivery import (
    add_business_days,
    dispatch_date,
    estimated_delivery,
    greek_holidays,
    is_business_day,
    orthodox_easter,
    parse_cutoff,
)
from shipping.models import ShippingRate

CUTOFF = time(15, 0)


def _rate(max_days: int | None) -> ShippingRate:
    return ShippingRate(
        delivery_days_min=None if max_days is None else 1,
        delivery_days_max=max_days,
    )


def _utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


@pytest.mark.parametrize(
    ("year", "expected"),
    [
        (2024, date(2024, 5, 5)),
        (2025, date(2025, 4, 20)),
        (2026, date(2026, 4, 12)),
        (2027, date(2027, 5, 2)),
    ],
)
def test_orthodox_easter(year, expected):
    assert orthodox_easter(year) == expected


def test_greek_holidays_include_movable_and_fixed_days():
    holidays = greek_holidays(2026)
    assert date(2026, 2, 23) in holidays  # Clean Monday
    assert date(2026, 4, 10) in holidays  # Good Friday
    assert date(2026, 4, 13) in holidays  # Easter Monday
    assert date(2026, 6, 1) in holidays  # Whit Monday
    assert date(2026, 10, 28) in holidays  # Ochi Day


def test_weekend_and_holiday_are_not_business_days():
    assert is_business_day(date(2026, 10, 5))  # Monday
    assert not is_business_day(date(2026, 10, 10))  # Saturday
    assert not is_business_day(date(2026, 10, 28))  # Wednesday holiday


def test_add_business_days_skips_weekend_and_easter():
    # Thu 9 Apr + 1: Good Friday, weekend, Easter Monday are skipped.
    assert add_business_days(date(2026, 4, 9), 1) == date(2026, 4, 14)


class TestDispatchDate:
    def test_before_cutoff_on_a_business_day_ships_same_day(self):
        # 10:00 EEST Monday
        assert dispatch_date(_utc(2026, 10, 5, 7, 0), CUTOFF) == date(
            2026, 10, 5
        )

    def test_exactly_at_cutoff_misses_it(self):
        # 15:00 EEST Monday
        assert dispatch_date(_utc(2026, 10, 5, 12, 0), CUTOFF) == date(
            2026, 10, 6
        )

    def test_one_minute_before_cutoff_makes_it(self):
        # 14:59 EEST Monday
        assert dispatch_date(_utc(2026, 10, 5, 11, 59), CUTOFF) == date(
            2026, 10, 5
        )

    def test_summer_offset_pushes_a_utc_morning_order_past_cutoff(self):
        # 12:30 UTC reads as before 15:00 naively, but is 15:30 in Athens.
        assert dispatch_date(_utc(2026, 10, 5, 12, 30), CUTOFF) == date(
            2026, 10, 6
        )

    def test_winter_offset_keeps_the_same_utc_time_before_cutoff(self):
        # 12:30 UTC is 14:30 EET on Monday 7 Dec.
        assert dispatch_date(_utc(2026, 12, 7, 12, 30), CUTOFF) == date(
            2026, 12, 7
        )

    def test_late_evening_utc_is_already_tomorrow_in_athens(self):
        # 22:30 UTC Sunday = 01:30 Monday in Athens: a business day,
        # before cutoff, so it ships Monday (not Tuesday).
        assert dispatch_date(_utc(2026, 10, 4, 22, 30), CUTOFF) == date(
            2026, 10, 5
        )

    def test_friday_after_cutoff_ships_monday(self):
        # 16:00 EEST Friday 9 Oct
        assert dispatch_date(_utc(2026, 10, 9, 13, 0), CUTOFF) == date(
            2026, 10, 12
        )

    def test_saturday_ships_monday(self):
        assert dispatch_date(_utc(2026, 10, 10, 8, 0), CUTOFF) == date(
            2026, 10, 12
        )

    def test_holiday_is_skipped(self):
        # Tue 27 Oct after cutoff -> Wed 28 is Ochi Day -> Thu 29.
        assert dispatch_date(_utc(2026, 10, 27, 14, 0), CUTOFF) == date(
            2026, 10, 29
        )

    def test_no_cutoff_ships_same_day_even_late(self):
        assert dispatch_date(_utc(2026, 10, 5, 20, 0), None) == date(
            2026, 10, 5
        )


class TestEstimatedDelivery:
    def test_adds_the_rates_max_business_days_to_dispatch(self):
        # Mon 10:00 EEST, dispatch Mon, +2 -> Wed.
        assert estimated_delivery(
            _rate(2), _utc(2026, 10, 5, 7, 0), CUTOFF
        ) == date(2026, 10, 7)

    def test_after_cutoff_shifts_the_estimate_by_a_business_day(self):
        assert estimated_delivery(
            _rate(2), _utc(2026, 10, 5, 12, 30), CUTOFF
        ) == date(2026, 10, 8)

    def test_zero_days_delivers_on_dispatch_day(self):
        assert estimated_delivery(
            _rate(0), _utc(2026, 10, 5, 7, 0), CUTOFF
        ) == date(2026, 10, 5)

    def test_rate_without_an_estimate_gives_none(self):
        assert (
            estimated_delivery(_rate(None), _utc(2026, 10, 5, 7, 0), CUTOFF)
            is None
        )

    def test_no_rate_gives_none(self):
        assert estimated_delivery(None, _utc(2026, 10, 5, 7, 0), CUTOFF) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("15:00", time(15, 0)), ("09:05", time(9, 5)), ("", None), (None, None)],
)
def test_parse_cutoff(raw, expected):
    assert parse_cutoff(raw) == expected
