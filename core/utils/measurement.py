from typing import Type

from measurement.base import BidimensionalMeasure
from measurement.base import MeasureBase


def get_measurement(
    measure: Type[MeasureBase | BidimensionalMeasure],
    value,
    unit: str = None,
    original_unit: str = None,
) -> MeasureBase | BidimensionalMeasure:
    unit = unit or measure.STANDARD_UNIT

    m = measure(**{unit: value})
    if original_unit:
        m.unit = original_unit
    if isinstance(m, BidimensionalMeasure):
        m.reference.value = 1
    return m
