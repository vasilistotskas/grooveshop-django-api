"""Contract tests for admin/displays.py helpers."""

from __future__ import annotations

from admin.displays import header_two_line


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
    assert row[3] == {"path": "/media/p.jpg", "squared": False}


def test_header_two_line_without_image_is_three_elements():
    row = header_two_line("Jane Doe", "jane@example.com")

    assert row == ["Jane Doe", "jane@example.com", "JD"]
