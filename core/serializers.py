import decimal
import importlib
from typing import Any
from typing import override
from typing import Type
from typing import TypedDict

from drf_spectacular.utils import extend_schema_field
from measurement.base import BidimensionalMeasure
from measurement.base import MeasureBase
from rest_framework import serializers

from product.models import Product


def is_valid_unit(unit_to_validate: str, measurement) -> bool:
    return unit_to_validate in measurement.get_units()


def is_valid_decimal(value_to_validate: str) -> bool:
    try:
        decimal.Decimal(value_to_validate)
        return True
    except decimal.InvalidOperation:
        return False


class Representation(TypedDict):
    unit: Any
    value: Any


@extend_schema_field(
    {
        "type": "object",
        "properties": {
            "unit": {"type": "string"},
            "value": {"type": "number"},
        },
        "example": {"unit": "kg", "value": 1.0},
    }
)
class MeasurementSerializerField(serializers.Field):
    default_error_messages = {
        "invalid_unit": "Invalid unit. '{invalid_unit}' is not a valid unit for "
        "{measurement}. Valid units are: {valid_units}.",
        "invalid_value": "Invalid value. '{invalid_value}' is not a valid decimal.",
        "missing_keys": "Missing required keys. 'unit' and 'value' are required.",
    }

    def __init__(self, measurement: Type[MeasureBase | BidimensionalMeasure], *args, **kwargs) -> None:
        super(MeasurementSerializerField, self).__init__(*args, **kwargs)
        self.measurement = measurement

    @override
    def to_representation(self, obj: Any) -> Representation:
        return {"unit": obj.unit, "value": obj.value}

    @override
    def to_internal_value(self, data: Representation) -> MeasureBase | BidimensionalMeasure:
        if not isinstance(data, dict) or "unit" not in data or "value" not in data:
            self.fail("missing_keys")

        unit = data["unit"]
        value = data["value"]

        if not is_valid_unit(unit, self.measurement):
            self.fail(
                "invalid_unit",
                invalid_unit=unit,
                measurement=self.measurement.__name__,
                valid_units=", ".join(self.measurement.get_units()),
            )

        if not is_valid_decimal(value):
            self.fail("invalid_value", invalid_value=value)

        return self.measurement(**{unit: value})


class ContentObjectRelatedField(serializers.RelatedField):
    @override
    def to_representation(self, value):
        if isinstance(value, Product):
            serializer = importlib.import_module("product.serializers.product").ProductSerializer(value)
        else:
            raise Exception("Unexpected type of object")

        return serializer.data
