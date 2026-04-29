"""Unit tests for ``_format_parcel_weight_kg`` in shipping_boxnow/services.py.

The helper converts the internally-stored integer grams to the
kilogram-decimal value BoxNow's delivery-request API expects in
``items[].weight``. Verified empirically: BoxNow's voucher template
prints the raw value verbatim with a ``kg`` label, so sending 189
prints "189.00 kg" — i.e. the API field is kilograms, not grams.
"""

from __future__ import annotations

from shipping_boxnow.services import (
    _BOXNOW_MAX_WEIGHT_KG,
    _format_parcel_weight_kg,
)


def test_grams_converted_to_kilograms():
    # 105 g (real prod product on order #43) → 0.105 kg
    assert _format_parcel_weight_kg(105) == 0.105


def test_typical_grocery_box_converted():
    # 5000 g (5 kg) → 5.0 kg
    assert _format_parcel_weight_kg(5000) == 5.0


def test_zero_returns_zero():
    """BoxNow PDF explicitly allows 0 ('if parcel weight unknown pass 0')."""
    assert _format_parcel_weight_kg(0) == 0.0


def test_none_returns_zero():
    """The model field can be NULL on legacy rows; treat as unknown."""
    assert _format_parcel_weight_kg(None) == 0.0


def test_negative_returns_zero():
    """Defensive: a corrupt negative value mustn't reach BoxNow."""
    assert _format_parcel_weight_kg(-10) == 0.0


def test_rounding_to_three_decimals():
    # 189.24 g (2 × 94.62 g, real local product) → 0.189 kg.
    # Internal storage rounds 189.24 → 189 (gram precision is enough).
    assert _format_parcel_weight_kg(189) == 0.189


def test_above_max_clamped_to_max():
    """A unit-confusion bug pushing weight far past BoxNow's cap should
    clamp instead of letting BoxNow 4xx and dead-letter.

    The cap is 10^6 kg per the API manual P421 — we only hit this if
    something upstream is very wrong (no real parcel reaches that)."""
    # 10^6 kg = 10^9 grams. Send 2 × 10^9 grams to trigger clamp.
    huge_grams = 2 * 1_000_000_000
    assert _format_parcel_weight_kg(huge_grams) == _BOXNOW_MAX_WEIGHT_KG


def test_clamp_logs_warning_on_overflow(caplog):
    """When the clamp engages, leave a breadcrumb so the operator can
    chase down the upstream unit bug."""
    import logging

    huge_grams = 2 * 1_000_000_000
    with caplog.at_level(logging.WARNING, logger="shipping_boxnow.services"):
        _format_parcel_weight_kg(huge_grams)
    assert any(
        "Clamping BoxNow parcel weight" in rec.message for rec in caplog.records
    )


def test_payload_value_matches_voucher_format():
    """Document the unit-relationship contract: whatever we send, BoxNow
    prints as ``N.NN kg`` on the voucher. So the value we send IS the
    kg the customer sees."""
    # 0.105 kg in → 0.105 kg printed by BoxNow → "0.11 kg" rounded
    # by their 2-decimal voucher template.
    assert _format_parcel_weight_kg(105) == 0.105
