import decimal
import importlib
from typing import Any, TypedDict

from drf_spectacular.utils import extend_schema_field
from measurement.base import BidimensionalMeasure, MeasureBase
from rest_framework import serializers

from product.models import Product


def is_valid_unit(
    unit_to_validate: str, measurement: type[MeasureBase | BidimensionalMeasure]
) -> bool:
    if issubclass(measurement, BidimensionalMeasure):
        if "__" in unit_to_validate:
            primary_unit, reference_unit = unit_to_validate.split("__")
            primary_units = (
                measurement.PRIMARY_DIMENSION.get_units()
                if hasattr(measurement, "PRIMARY_DIMENSION")
                else {}
            )
            reference_units = (
                measurement.REFERENCE_DIMENSION.get_units()
                if hasattr(measurement, "REFERENCE_DIMENSION")
                else {}
            )
            return (
                primary_unit in primary_units
                and reference_unit in reference_units
            )
        else:
            # Handle special cases like "mph", "kph" that don't use the "__" format
            # These are common in bidimensional measures but need special handling
            # This is a simplified approach - in a full implementation we might check against known aliases
            special_units = ["mph", "kph", "mps", "fps"]
            return unit_to_validate in special_units
    elif hasattr(measurement, "get_units"):
        return unit_to_validate in measurement.get_units()
    return False


def is_valid_decimal(value_to_validate: str) -> bool:
    try:
        decimal.Decimal(value_to_validate)
        return True
    except decimal.InvalidOperation:
        return False


class Representation(TypedDict):
    unit: Any
    value: Any


class ContentObjectRelatedField(serializers.RelatedField):
    def to_representation(self, value):
        if isinstance(value, Product):
            serializer = importlib.import_module(
                "product.serializers.product"
            ).ProductSerializer(value)
        else:
            raise Exception("Unexpected type of object")

        return serializer.data


@extend_schema_field(
    {
        "type": "object",
        "properties": {
            "database": {"type": "boolean"},
            "redis": {"type": "boolean"},
            "celery": {"type": "boolean"},
        },
        "example": {"database": True, "redis": True, "celery": True},
    }
)
class HealthCheckResponseSerializer(serializers.Serializer):
    database = serializers.BooleanField()
    redis = serializers.BooleanField()
    celery = serializers.BooleanField()


@extend_schema_field(
    {
        "type": "object",
        "properties": {
            "detail": {"type": "string"},
            "error": {"type": "string", "required": False},
        },
        "example": {"detail": "Error message", "error": "Error code"},
    }
)
class ErrorResponseSerializer(serializers.Serializer):
    detail = serializers.CharField()
    error = serializers.CharField(required=False)


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

    def __init__(
        self,
        measurement: type[MeasureBase | BidimensionalMeasure],
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.measurement = measurement

    def to_representation(self, obj: Any) -> Representation:
        if hasattr(obj, "unit") and hasattr(obj, "value"):
            return {"unit": obj.unit, "value": obj.value}
        elif isinstance(obj, decimal.Decimal | float | int):
            return {
                "value": float(obj),
                "unit": self.measurement.STANDARD_UNIT
                if hasattr(self.measurement, "STANDARD_UNIT")
                else "unknown",
            }
        else:
            return {"value": str(obj), "unit": "unknown"}

    def to_internal_value(self, data: Representation):
        if (
            not isinstance(data, dict)
            or "unit" not in data
            or "value" not in data
        ):
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
