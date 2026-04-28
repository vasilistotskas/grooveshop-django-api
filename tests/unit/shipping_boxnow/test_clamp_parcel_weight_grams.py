"""Unit tests for ``_clamp_parcel_weight_grams`` in shipping_boxnow/services.py.

The helper guards the delivery-request payload's ``items[].weight``
field against BoxNow's P421 ("Invalid parcel weight, must be between 0
and 10^6") so a corrupt model value can't dead-letter the whole order.
"""

from __future__ import annotations

from shipping_boxnow.services import (
    _BOXNOW_MAX_WEIGHT_GRAMS,
    _clamp_parcel_weight_grams,
)


def test_normal_weight_passes_through():
    assert _clamp_parcel_weight_grams(105) == 105


def test_typical_grocery_box_passes_through():
    """A 5 kg box (5000 g) is well under the cap."""
    assert _clamp_parcel_weight_grams(5000) == 5000


def test_zero_passes_through():
    """BoxNow PDF explicitly allows 0 ('if parcel weight unknown pass 0')."""
    assert _clamp_parcel_weight_grams(0) == 0


def test_none_returns_zero():
    """The model field can be NULL on legacy rows; treat as unknown."""
    assert _clamp_parcel_weight_grams(None) == 0


def test_negative_returns_zero():
    """Defensive: a corrupt negative value mustn't reach BoxNow."""
    assert _clamp_parcel_weight_grams(-10) == 0


def test_exactly_max_passes_through():
    """Boundary: the cap itself is valid."""
    assert (
        _clamp_parcel_weight_grams(_BOXNOW_MAX_WEIGHT_GRAMS)
        == _BOXNOW_MAX_WEIGHT_GRAMS
    )


def test_above_max_clamped_to_max():
    """A unit-confusion bug (e.g. someone accidentally storing kg as
    grams times 1000) would push the value far above the cap; we
    clamp instead of letting BoxNow 4xx and dead-letter."""
    assert _clamp_parcel_weight_grams(2_000_000) == _BOXNOW_MAX_WEIGHT_GRAMS


def test_clamp_logs_warning_on_overflow(caplog):
    """When the clamp engages, it should leave a breadcrumb so the
    operator can chase down the upstream unit bug."""
    import logging

    with caplog.at_level(logging.WARNING, logger="shipping_boxnow.services"):
        _clamp_parcel_weight_grams(2_000_000)
    assert any(
        "Clamping BoxNow parcel weight" in rec.message for rec in caplog.records
    )
