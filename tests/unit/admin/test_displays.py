"""Contract tests for admin/displays.py helpers."""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone, translation

from admin.displays import header_two_line, relative_time


def test_header_two_line_puts_image_dict_at_index_three():
    """Unfold's ``display_header.html`` is positional: ``value.2`` is
    the initials circle (rendered as raw text), ``value.3`` is the
    image dict. An image dict at index 2 prints ``{'path': ...}`` in
    the avatar slot — the broken-product-images bug (prod 2026-07-12).
    """
    row = header_two_line(
        "Organic T-Shirt", "SKU 123", image_path="/media/p.jpg"
    )

    assert len(row) == 4
    assert row[2] == "OT"  # initials fallback stays at index 2
    assert row[3] == {
        "path": "/media/p.jpg",
        "squared": False,
        "as_background": False,
    }


def test_header_two_line_without_image_is_three_elements():
    row = header_two_line("Jane Doe", "jane@example.com")

    assert row == ["Jane Doe", "jane@example.com", "JD"]


def test_relative_time_follows_the_active_language():
    """Django's own Greek catalogue leaves the ``timesince`` units
    empty, so without our strings the Greek admin read "5 days"."""
    now = timezone.now()
    then = now - timedelta(days=5)

    with translation.override("el"):
        assert relative_time(then, now) == "5\xa0ημέρες"
    with translation.override("en"):
        assert relative_time(then, now) == "5\xa0days"


def test_money_uses_the_bound_stores_currency(settings):
    from unittest import mock

    from admin.displays import money, store_currency

    with translation.override("en"):
        assert money(1234.5) == "€1,234.50"
        settings.DEFAULT_CURRENCY = "USD"
        assert store_currency() == "USD"
        assert money(1234.5) == "$1,234.50"

    store = mock.Mock(default_currency="GBP")
    with (
        mock.patch("admin.displays.bound_tenant", return_value=store),
        translation.override("en"),
    ):
        assert store_currency() == "GBP"
        assert money(2) == "£2.00"


def test_the_chart_axis_formats_in_the_stores_currency():
    import json

    from admin.dashboard.base import bar_chart

    chart = bar_chart(["a"], [1.0], label="x", currency=True)

    ticks = json.loads(chart["options"])["scales"]["y"]["ticks"]
    assert ticks["format"]["currency"] == "EUR"
