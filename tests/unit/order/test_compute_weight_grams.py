"""Unit tests for ``_compute_total_weight_grams`` in order/services.py.

The helper computes the parcel weight that lands on
``BoxNowShipment.weight_grams`` (and from there into the BoxNow
delivery-request payload at `items[].weight`). BoxNow tariffs by
weight bracket, so getting this right matters for billing accuracy
as well as the printed voucher.
"""

from __future__ import annotations

from unittest.mock import Mock

from order.services import _compute_total_weight_grams


def _mk_product(grams: float | None) -> Mock:
    """Build a stand-in for ``product.Product`` with just the weight
    surface ``_compute_total_weight_grams`` reads.

    The real ``MeasurementField``'s ``Mass`` type has ``STANDARD_UNIT='g'``
    and exposes ``.g`` for the value in grams — mirror that here.
    """
    p = Mock()
    if grams is None:
        p.weight = None
    else:
        p.weight = Mock()
        p.weight.g = grams
    return p


def test_single_item_grams_rounded():
    p = _mk_product(105.0)  # 105 g, like the real prod product on order #43
    assert _compute_total_weight_grams([(p, 1)]) == 105


def test_quantity_multiplies_weight():
    p = _mk_product(250.0)
    assert _compute_total_weight_grams([(p, 4)]) == 1000


def test_multiple_items_sum():
    a = _mk_product(105.0)
    b = _mk_product(250.0)
    assert _compute_total_weight_grams([(a, 2), (b, 3)]) == 210 + 750


def test_missing_weight_falls_back_to_zero():
    a = _mk_product(None)
    b = _mk_product(105.0)
    # Missing-weight products contribute 0; total = b only.
    assert _compute_total_weight_grams([(a, 5), (b, 1)]) == 105


def test_empty_iterable_returns_zero():
    assert _compute_total_weight_grams([]) == 0


def test_zero_quantity_skipped():
    p = _mk_product(500.0)
    assert _compute_total_weight_grams([(p, 0)]) == 0


def test_none_product_skipped():
    p = _mk_product(500.0)
    # ``items_data.get("product")`` can be None on malformed input —
    # the helper must not crash, and missing product contributes 0.
    assert _compute_total_weight_grams([(None, 3), (p, 1)]) == 500


def test_rounds_to_nearest_int_gram():
    # 94.62 g (a real local product weight) should round to 95.
    p = _mk_product(94.62)
    assert _compute_total_weight_grams([(p, 1)]) == 95


def test_returns_non_negative_for_garbage_input():
    """Defensive: a malformed weight value mustn't produce a negative
    integer that would later trip BoxNow's payload validation."""
    p = Mock()
    p.weight = Mock()
    p.weight.g = -1000.0  # nonsensical but possible if a factory drifts
    assert _compute_total_weight_grams([(p, 2)]) == 0
