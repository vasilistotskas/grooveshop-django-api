import decimal
import importlib
from typing import Any, TypedDict
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from measurement.base import BidimensionalMeasure, MeasureBase
from rest_framework import serializers

from product.models import Product


class RequiredDefaultTranslationMixin:
    """Enforce that the configured default-language translation is present.

    Parler treats translations as optional at the model layer, so any
    serializer that needs a guaranteed default-language entry mixes this
    in and sets ``required_translation_field`` to the translated field
    that must be non-empty (e.g. ``"name"``).

    The default language is read from
    ``settings.PARLER_DEFAULT_LANGUAGE_CODE`` rather than hardcoded, so
    the rule follows the configured locales — adding or changing
    languages on either end needs no change here. Non-default languages
    stay optional.
    """

    required_translation_field: str | None = None

    def validate_translations(self, value):
        if not value:
            raise serializers.ValidationError(
                _("At least one translation is required.")
            )

        default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE
        default_translation = value.get(default_language)
        if not default_translation:
            raise serializers.ValidationError(
                _(
                    "A translation for the default language (%(lang)s) "
                    "is required."
                )
                % {"lang": default_language}
            )

        field = self.required_translation_field
        if field and not default_translation.get(field):
            raise serializers.ValidationError(
                _(
                    "The '%(field)s' field is required for the default "
                    "language (%(lang)s)."
                )
                % {"field": field, "lang": default_language}
            )

        return value


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


@extend_schema_field(
    {
        "type": "object",
        "description": _(
            "Serialized representation of the related content object"
        ),
        "properties": {
            "id": {"type": "integer", "example": 1},
            "name": {"type": "string", "example": "Sample Product"},
            "description": {"type": "string", "example": "Product description"},
            "price": {"type": "string", "example": "29.99"},
            "active": {"type": "boolean", "example": True},
        },
        "additionalProperties": True,
    }
)
class ContentObjectRelatedField(serializers.RelatedField):
    SERIALIZER_REGISTRY: dict[type, str] = {
        Product: "product.serializers.product.ProductSerializer",
    }

    def to_representation(self, value):
        for model_cls, serializer_path in self.SERIALIZER_REGISTRY.items():
            if isinstance(value, model_cls):
                module_path, cls_name = serializer_path.rsplit(".", 1)
                module = importlib.import_module(module_path)
                serializer_cls = getattr(module, cls_name)
                return serializer_cls(value).data

        raise ValueError(f"Unexpected type of object: {type(value).__name__}")


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


class DetailSerializer(serializers.Serializer):
    detail = serializers.CharField()


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
class ErrorResponseSerializer(DetailSerializer):
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

    def to_representation(self, value: Any) -> Representation:
        if hasattr(value, "unit") and hasattr(value, "value"):
            return {"unit": value.unit, "value": value.value}
        elif isinstance(value, decimal.Decimal | float | int):
            return {
                "value": float(value),
                "unit": self.measurement.STANDARD_UNIT
                if hasattr(self.measurement, "STANDARD_UNIT")
                else "unknown",
            }
        else:
            return {"value": str(value), "unit": "unknown"}

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


@extend_schema_field(
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "example": "CHECKOUT_SHIPPING_PRICE"},
            "value": {"type": "string", "example": "3.00"},
            "type": {"type": "string", "example": "string"},
        },
        "example": {
            "name": "CHECKOUT_SHIPPING_PRICE",
            "value": "3.00",
            "type": "string",
        },
    }
)
class SettingSerializer(serializers.Serializer):
    name = serializers.CharField()
    value = serializers.CharField()
    type = serializers.CharField()


@extend_schema_field(
    {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "value": {"type": "string"},
        },
        "example": {"name": "CHECKOUT_SHIPPING_PRICE", "value": "3.00"},
    }
)
class SettingDetailSerializer(serializers.Serializer):
    name = serializers.CharField()
    value = serializers.CharField()
