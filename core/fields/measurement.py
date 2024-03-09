import logging
from typing import Any
from typing import Optional
from typing import Sequence
from typing import Type

from django.db.models import FloatField
from django.db.models import Model
from django.forms.fields import Field
from django.utils.translation import gettext_lazy as _
from measurement.base import BidimensionalMeasure
from measurement.base import MeasureBase

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
            "'%(value)s' (%(type_given)s) value" " must be of type %(type_wanted)s."
        ),
    }

    def __init__(
        self,
        verbose_name: Optional[str] = None,
        name: Optional[str] = None,
        measurement: Type[MeasureBase | BidimensionalMeasure] = None,
        unit_choices: Optional[list[tuple[str, str]]] = None,
        *args,
        **kwargs,
    ):
        if measurement is None or not issubclass(measurement, self.MEASURE_BASES):
            raise MeasurementTypeError(
                "MeasurementField requires a measurement subclass of MeasureBase."
            )

        self.measurement = measurement
        self.widget_args = {
            "measurement": measurement,
            "unit_choices": unit_choices,
        }

        super(MeasurementField, self).__init__(verbose_name, name, *args, **kwargs)

    def deconstruct(self) -> tuple[str, str, Sequence, dict[str, Any]]:
        name, path, args, kwargs = super(MeasurementField, self).deconstruct()
        kwargs["measurement"] = self.measurement
        return name, path, args, kwargs

    def get_prep_value(self, value) -> Optional[float]:
        if value is None:
            return None

        elif isinstance(value, self.MEASURE_BASES):
            return float(value.standard)

        else:
            return super(MeasurementField, self).get_prep_value(value)

    def get_default_unit(self) -> str:
        unit_choices = self.widget_args["unit_choices"]
        if unit_choices:
            return unit_choices[0][0]
        return self.measurement.STANDARD_UNIT

    def from_db_value(
        self, value: Optional[float], *args, **kwargs
    ) -> Optional[BidimensionalMeasure]:
        if value is None:
            return None

        return get_measurement(
            measure=self.measurement,
            value=value,
            original_unit=self.get_default_unit(),
        )

    def value_to_string(self, obj: Model) -> str:
        value = self.value_from_object(obj)
        if not isinstance(value, self.MEASURE_BASES):
            return value
        return "%s:%s" % (value.value, value.unit)

    def deserialize_value_from_string(
        self, value_str: str
    ) -> Optional[MeasureBase | BidimensionalMeasure]:
        try:
            value, unit = value_str.split(":", 1)
            value = float(value)
            measure = get_measurement(self.measurement, value=value, unit=unit)
            return measure
        except ValueError as e:
            logger.error(f"Error deserializing measurement: {e}")
            return None

    def to_python(self, value: Any) -> Optional[MeasureBase | BidimensionalMeasure]:
        if value is None:
            return value
        elif isinstance(value, self.MEASURE_BASES):
            return value
        elif isinstance(value, str):
            parsed = self.deserialize_value_from_string(value)
            if parsed is not None:
                return parsed
        value = super(MeasurementField, self).to_python(value)

        return_unit = self.get_default_unit()

        msg = (
            'You assigned a %s instead of %s to %s.%s.%s, unit was guessed to be "%s".'
            % (
                type(value).__name__,
                str(self.measurement.__name__),
                self.model.__module__,
                self.model.__name__,
                self.name,
                return_unit,
            )
        )
        logger.warning(msg)
        return get_measurement(
            measure=self.measurement,
            value=value,
            unit=return_unit,
        )

    def formfield(self, **kwargs) -> Field:
        defaults = {"form_class": MeasurementFormField}
        defaults.update(kwargs)
        defaults.update(self.widget_args)
        return super(MeasurementField, self).formfield(**defaults)
