"""Shipping-domain helpers shared by carrier adapters."""

from __future__ import annotations


def compute_total_weight_grams(items) -> int:
    """Sum the parcel weight (in grams) from an iterable of (product, qty) pairs.

    BoxNow expects ``items[].weight`` in grams as a non-negative
    integer (per BoxNow API §3.4). Each Product carries a
    ``MeasurementField`` whose underlying ``measurement.measures.Mass``
    type uses ``STANDARD_UNIT = 'g'`` — reading ``weight.g`` returns
    the value in grams regardless of the unit it was originally stored
    in (kg, lb, oz all convert through the standard).

    Used at shipment-creation time across BoxNow + ACS adapters so the
    voucher PDF prints the real parcel weight instead of "0.00 kg" —
    couriers tariff by weight bracket, so getting this right matters
    for billing accuracy too.

    Falls back to 0 only when the product has no weight set; never
    raises so missing data can't block order creation.
    """
    total_grams = 0.0
    for product, quantity in items:
        if not product or not quantity:
            continue
        weight = getattr(product, "weight", None)
        if not weight:
            continue
        grams_per_unit = float(getattr(weight, "g", 0) or 0)
        total_grams += grams_per_unit * float(quantity)
    return max(0, int(round(total_grams)))
