import logging
from typing import Any, Optional, override

from django.db.models import FloatField, Model
from django.utils.translation import gettext_lazy as _
from measurement.base import BidimensionalMeasure, MeasureBase

from core.forms.measurement import MeasurementFormField
from core.utils.measurement import get_measurement

logger = logging.getLogger(__name__)


class MeasurementTypeError(TypeError):
    pass


class MeasurementField(FloatField):
    description = "Easily store, retrieve, and convert python measures."
    empty_strings_allowed = False
    MEASURE_BASES = (
        BidimensionalMeasure,
        MeasureBase,
    )
    default_error_messages = {
        "invalid_type": _(
            "'%(value)s' (%(type_given)s) value"
            " must be of type %(type_wanted)s."
        ),
    }

    def __init__(
        self,
        verbose_name: Optional[str] = None,
        name: Optional[str] = None,
        measurement: Optional[type[MeasureBase | BidimensionalMeasure]] = None,
        unit_choices: Optional[list[tuple[str, str]]] = None,
        *args,
        **kwargs,
    ):
        if measurement is None or not issubclass(
            measurement, self.MEASURE_BASES
        ):
            raise MeasurementTypeError(
                "MeasurementField requires a measurement subclass of MeasureBase."
            )

        self.measurement = measurement
        self.widget_args = {
            "measurement": measurement,
            "unit_choices": unit_choices,
        }

        super().__init__(verbose_name, name, *args, **kwargs)

    @override
    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        kwargs["measurement"] = self.measurement
        return name, path, args, kwargs

    @override
    def get_prep_value(self, value):
        if value is None:
            return None

        elif isinstance(value, self.MEASURE_BASES):
            return float(value.standard)

        else:
            return super().get_prep_value(value)

    def get_default_unit(self):
        unit_choices = self.widget_args["unit_choices"]
        if unit_choices:
            return unit_choices[0][0]
        return self.measurement.STANDARD_UNIT

    def from_db_value(self, value: Optional[float], *args, **kwargs):
        if value is None:
            return None

        return get_measurement(
            measure=self.measurement,
            value=value,
            original_unit=self.get_default_unit(),
        )

    @override
    def value_to_string(self, obj: Model):
        value = self.value_from_object(obj)
        if not isinstance(value, self.MEASURE_BASES):
            return value
        return "{}:{}".format(value.value, value.unit)

    def deserialize_value_from_string(self, value_str: str):
        try:
            value, unit = value_str.split(":", 1)
            value = float(value)
            measure = get_measurement(self.measurement, value=value, unit=unit)
            return measure
        except ValueError as e:
            logger.error(f"Error deserializing measurement: {e}")
            return None

    @override
    def to_python(self, value: Any):
        if value is None or isinstance(value, self.MEASURE_BASES):
            return value
        elif isinstance(value, str):
            parsed = self.deserialize_value_from_string(value)
            if parsed is not None:
                return parsed
        value = super().to_python(value)

        return_unit = self.get_default_unit()

        msg = 'You assigned a {} instead of {} to {}.{}.{}, unit was guessed to be "{}".'.format(
            type(value).__name__,
            str(self.measurement.__name__),
            self.model.__module__,
            self.model.__name__,
            self.name,
            return_unit,
        )
        logger.warning(msg)
        return get_measurement(
            measure=self.measurement,
            value=value,
            unit=return_unit,
        )

    @override
    def formfield(self, **kwargs):
        defaults = {"form_class": MeasurementFormField}
        defaults.update(kwargs)
        defaults.update(self.widget_args)
        return super().formfield(**defaults)
