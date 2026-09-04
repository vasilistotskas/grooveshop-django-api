"""Unit vocabularies for ``MeasurementField``.

Only weight is modelled: ``Product.weight`` is the one measurement
this platform stores. Distance, area and volume enums lived here too,
along with a ``MeasurementUnits`` type built by folding all four
together, and nothing ever read them.
"""

from enum import StrEnum


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
