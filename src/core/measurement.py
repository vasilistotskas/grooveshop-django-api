"""Measurements: the unit vocabularies ``MeasurementField`` stores and the
one constructor every field and form builds a measure with.

Weight (``Product.weight``) and distance (``Product.length`` / ``width``
/ ``height``) are the measurements this platform stores. The model field
is ``core.fields.measurement.MeasurementField`` and the admin's form field
``core.forms.measurement.MeasurementFormField``; ``core.weight`` holds the
default that migrations name by path.
"""

from enum import StrEnum

from measurement.base import BidimensionalMeasure, MeasureBase


class WeightUnits(StrEnum):
    G = "g"
    LB = "lb"
    OZ = "oz"
    KG = "kg"
    TONNE = "tonne"


WeightUnits.CHOICES = [
    (WeightUnits.G, "Gram"),
    (WeightUnits.LB, "Pound"),
    (WeightUnits.OZ, "Ounce"),
    (WeightUnits.KG, "kg"),
    (WeightUnits.TONNE, "Tonne"),
]


class DistanceUnits(StrEnum):
    CM = "cm"
    MM = "mm"
    M = "m"
    INCH = "inch"


DistanceUnits.CHOICES = [
    (DistanceUnits.CM, "cm"),
    (DistanceUnits.MM, "mm"),
    (DistanceUnits.M, "m"),
    (DistanceUnits.INCH, "inch"),
]


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
