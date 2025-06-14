import logging
from typing import Any

from django.db.models import FloatField, Model
from django.forms import ChoiceField, Field
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
        verbose_name: str | None = None,
        name: str | None = None,
        measurement: type[MeasureBase | BidimensionalMeasure] | None = None,
        unit_choices: list[tuple[str, str]] | None = None,
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

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        kwargs["measurement"] = self.measurement
        return name, path, args, kwargs

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

    def from_db_value(self, value: float | None, *args, **kwargs):
        if value is None:
            return None

        return get_measurement(
            measure=self.measurement,
            value=value,
            original_unit=self.get_default_unit(),
        )

    def value_to_string(self, obj: Model):
        value = self.value_from_object(obj)
        if not isinstance(value, self.MEASURE_BASES):
            return value
        return "{}:{}".format(value.value, value.unit)

    def deserialize_value_from_string(self, value_str: str):
        try:
            value_part, unit = value_str.split(":", 1)
            value_float = float(value_part)
            measure = get_measurement(
                self.measurement, value=value_float, unit=unit
            )
            return measure
        except ValueError as e:
            logger.error(f"Error deserializing measurement: {e}")
            return None

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

    def formfield(
        self,
        form_class: type[Field] | None = None,
        choices_form_class: type[ChoiceField] | None = None,
        **kwargs: Any,
    ) -> Field | None:
        if form_class is None:
            form_class = MeasurementFormField

        kwargs.update(self.widget_args)

        return super().formfield(
            form_class=form_class,
            choices_form_class=choices_form_class,
            **kwargs,
        )
