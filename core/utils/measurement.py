from measurement.base import BidimensionalMeasure, MeasureBase


def get_measurement(
    measure: type[MeasureBase | BidimensionalMeasure],
    value,
    unit: str | None = None,
    original_unit: str | None = None,
):
    unit = unit or measure.STANDARD_UNIT

    m = measure(**{unit: value})
    if original_unit:
        m.unit = original_unit
    if isinstance(m, BidimensionalMeasure):
        m.reference.value = 1
    return m
