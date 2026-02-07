from enum import StrEnum


class DistanceUnits(StrEnum):
    MM = "mm"
    CM = "cm"
    DM = "dm"
    M = "m"
    KM = "km"
    FT = "ft"
    YD = "yd"
    INCH = "inch"


DistanceUnits.CHOICES = [
    (DistanceUnits.MM, "Millimeter"),
    (DistanceUnits.CM, "Centimeter"),
    (DistanceUnits.DM, "Decimeter"),
    (DistanceUnits.M, "Meter"),
    (DistanceUnits.KM, "Kilometers"),
    (DistanceUnits.FT, "Feet"),
    (DistanceUnits.YD, "Yard"),
    (DistanceUnits.INCH, "Inch"),
]


class AreaUnits(StrEnum):
    SQ_MM = "sq_mm"
    SQ_CM = "sq_cm"
    SQ_DM = "sq_dm"
    SQ_M = "sq_m"
    SQ_KM = "sq_km"
    SQ_FT = "sq_ft"
    SQ_YD = "sq_yd"
    SQ_INCH = "sq_inch"


AreaUnits.CHOICES = [
    (AreaUnits.SQ_MM, "Square millimeter"),
    (AreaUnits.SQ_CM, "Square centimeters"),
    (AreaUnits.SQ_DM, "Square decimeter"),
    (AreaUnits.SQ_M, "Square meters"),
    (AreaUnits.SQ_KM, "Square kilometers"),
    (AreaUnits.SQ_FT, "Square feet"),
    (AreaUnits.SQ_YD, "Square yards"),
    (AreaUnits.SQ_INCH, "Square inches"),
]


class VolumeUnits(StrEnum):
    CUBIC_MILLIMETER = "cubic_millimeter"
    CUBIC_CENTIMETER = "cubic_centimeter"
    CUBIC_DECIMETER = "cubic_decimeter"
    CUBIC_METER = "cubic_meter"
    LITER = "liter"
    CUBIC_FOOT = "cubic_foot"
    CUBIC_INCH = "cubic_inch"
    CUBIC_YARD = "cubic_yard"
    QT = "qt"
    PINT = "pint"
    FL_OZ = "fl_oz"
    ACRE_IN = "acre_in"
    ACRE_FT = "acre_ft"


VolumeUnits.CHOICES = [
    (VolumeUnits.CUBIC_MILLIMETER, "Cubic millimeter"),
    (VolumeUnits.CUBIC_CENTIMETER, "Cubic centimeter"),
    (VolumeUnits.CUBIC_DECIMETER, "Cubic decimeter"),
    (VolumeUnits.CUBIC_METER, "Cubic meter"),
    (VolumeUnits.LITER, "Liter"),
    (VolumeUnits.CUBIC_FOOT, "Cubic foot"),
    (VolumeUnits.CUBIC_INCH, "Cubic inch"),
    (VolumeUnits.CUBIC_YARD, "Cubic yard"),
    (VolumeUnits.QT, "Quart"),
    (VolumeUnits.PINT, "Pint"),
    (VolumeUnits.FL_OZ, "Fluid ounce"),
    (VolumeUnits.ACRE_IN, "Acre inch"),
    (VolumeUnits.ACRE_FT, "Acre feet"),
]


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


def prepare_all_units_dict():
    measurement_dict = {
        unit.upper(): unit
        for unit_choices in [
            DistanceUnits.CHOICES,
            AreaUnits.CHOICES,
            VolumeUnits.CHOICES,
            WeightUnits.CHOICES,
        ]
        for unit, _ in unit_choices
    }
    return dict(
        measurement_dict, CHOICES=[(v, v) for v in measurement_dict.values()]
    )


MeasurementUnits = type("MeasurementUnits", (object,), prepare_all_units_dict())
