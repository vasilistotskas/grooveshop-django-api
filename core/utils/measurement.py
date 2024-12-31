from typing import Optional

from measurement.base import BidimensionalMeasure, MeasureBase


def get_measurement(
    measure: type[MeasureBase | BidimensionalMeasure],
    value,
    unit: Optional[str] = None,
    original_unit: Optional[str] = None,
):
    unit = unit or measure.STANDARD_UNIT

    m = measure(**{unit: value})
    if original_unit:
        m.unit = original_unit
    if isinstance(m, BidimensionalMeasure):
        m.reference.value = 1
    return m
