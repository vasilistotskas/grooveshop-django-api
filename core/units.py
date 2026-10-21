"""Unit vocabularies for ``MeasurementField``.

Weight (``Product.weight``) and distance (``Product.length`` / ``width``
/ ``height``) are the measurements this platform stores. Area and volume
enums lived here too, along with a ``MeasurementUnits`` type built by
folding all of them together, and nothing ever read them.
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
