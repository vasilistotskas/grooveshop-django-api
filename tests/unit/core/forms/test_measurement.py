from unittest.mock import MagicMock, patch

from django import forms
from django.test import TestCase
from measurement.measures import Distance, Speed, Weight

from core.forms.measurement import MeasurementFormField, MeasurementWidget


class MockMeasureBase:
    STANDARD_UNIT = "kg"
    LABELS = {"kg": "Kilogram", "g": "Gram", "lb": "Pound"}

    @classmethod
    def get_units(cls):
        return ["kg", "g", "lb"]

    def __init__(self, **kwargs):
        for unit, value in kwargs.items():
            setattr(self, unit, value)
        self.unit = list(kwargs.keys())[0]
        self.value = list(kwargs.values())[0]


class MockBidimensionalMeasure:
    STANDARD_UNIT = "kph"

    class PRIMARY_DIMENSION:
        LABELS = {"km": "Kilometer", "m": "Meter"}

        @staticmethod
        def get_units():
            return ["km", "m"]

    class REFERENCE_DIMENSION:
        LABELS = {"hr": "Hour", "s": "Second"}

        @staticmethod
        def get_units():
            return ["hr", "s"]

    @classmethod
    def get_units(cls):
        return ["kph", "mps"]

    def __init__(self, **kwargs):
        for unit, value in kwargs.items():
            setattr(self, unit, value)
        self.unit = list(kwargs.keys())[0]
        self.value = list(kwargs.values())[0]


class TestMeasurementWidget(TestCase):
    def setUp(self):
        self.unit_choices = [("kg", "Kilogram"), ("g", "Gram"), ("lb", "Pound")]

    @patch("core.forms.measurement.UNFOLD_AVAILABLE", False)
    def test_widget_init_without_unfold(self):
        widget = MeasurementWidget(unit_choices=self.unit_choices)

        self.assertEqual(len(widget.widgets), 2)
        self.assertIsInstance(widget.widgets[0], forms.TextInput)
        self.assertIsInstance(widget.widgets[1], forms.Select)
        self.assertEqual(widget.unit_choices, self.unit_choices)

    @patch("core.forms.measurement.UNFOLD_AVAILABLE", True)
    @patch("core.forms.measurement.UnfoldAdminTextInputWidget")
    @patch("core.forms.measurement.UnfoldAdminSelectWidget")
    def test_widget_init_with_unfold(
        self, mock_select_widget, mock_text_widget
    ):
        mock_text_widget.return_value = MagicMock()
        mock_select_widget.return_value = MagicMock()

        MeasurementWidget(unit_choices=self.unit_choices)

        mock_text_widget.assert_called_once()
        mock_select_widget.assert_called_once()

    def test_widget_init_with_custom_widgets(self):
        custom_float_widget = forms.NumberInput()
        custom_unit_widget = forms.RadioSelect()

        widget = MeasurementWidget(
            float_widget=custom_float_widget,
            unit_choices_widget=custom_unit_widget,
            unit_choices=self.unit_choices,
        )

        self.assertEqual(widget.widgets[0], custom_float_widget)
        self.assertEqual(widget.widgets[1], custom_unit_widget)

    def test_decompress_with_none_value(self):
        widget = MeasurementWidget(unit_choices=self.unit_choices)
        result = widget.decompress(None)
        self.assertEqual(result, [None, None])

    def test_decompress_with_empty_string(self):
        widget = MeasurementWidget(unit_choices=self.unit_choices)
        result = widget.decompress("")
        self.assertEqual(result, [None, None])

    def test_decompress_with_literal_eval_string(self):
        widget = MeasurementWidget(unit_choices=self.unit_choices)
        result = widget.decompress("(10.5, 'kg')")
        self.assertEqual(result, [10.5, "kg"])

    def test_decompress_with_space_separated_string(self):
        widget = MeasurementWidget(unit_choices=self.unit_choices)
        result = widget.decompress("15.0 lb")
        self.assertEqual(result, [15.0, "lb"])

    def test_decompress_with_string_parsing_error(self):
        widget = MeasurementWidget(unit_choices=self.unit_choices)

        with self.assertRaises(ValueError):
            widget.decompress("invalid string format")

    def test_decompress_with_unparseable_number(self):
        widget = MeasurementWidget(unit_choices=self.unit_choices)

        with self.assertRaises(ValueError):
            widget.decompress("invalid kg")

    def test_decompress_with_measure_base_object(self):
        widget = MeasurementWidget(unit_choices=self.unit_choices)

        weight = Weight(kg=25.5)
        result = widget.decompress(weight)

        self.assertEqual(result[0], 25500.0)
        self.assertEqual(result[1], "g")

    def test_decompress_with_measure_base_unit_not_in_choices(self):
        limited_choices = [("g", "Gram")]
        widget = MeasurementWidget(unit_choices=limited_choices)

        weight = Weight(kg=25.5)
        result = widget.decompress(weight)

        self.assertEqual(result[0], 25500.0)
        self.assertEqual(result[1], "g")


class TestMeasurementFormField(TestCase):
    def test_init_with_invalid_measurement_type(self):
        with self.assertRaises(ValueError) as cm:
            MeasurementFormField(measurement=str)

        self.assertIn("must be a subclass of MeasureBase", str(cm.exception))

    def test_init_with_regular_measurement(self):
        field = MeasurementFormField(measurement=Weight)

        self.assertEqual(field.measurement, Weight)
        self.assertEqual(len(field.fields), 2)
        self.assertIsInstance(field.fields[0], forms.FloatField)
        self.assertIsInstance(field.fields[1], forms.ChoiceField)

    def test_init_with_bidimensional_measurement(self):
        field = MeasurementFormField(measurement=Speed)

        self.assertEqual(field.measurement, Speed)
        choices = field.fields[1].choices
        self.assertTrue(any("__" in choice[0] for choice in choices))

    def test_init_bidimensional_with_custom_separator(self):
        field = MeasurementFormField(
            measurement=Speed, bidimensional_separator=" per "
        )

        choices = field.fields[1].choices
        self.assertTrue(any(" per " in choice[1] for choice in choices))

    def test_init_bidimensional_with_invalid_separator(self):
        with self.assertRaises(AssertionError):
            MeasurementFormField(
                measurement=Speed,
                bidimensional_separator=123,
            )

    def test_init_with_custom_unit_choices(self):
        custom_choices = [("kg", "Kilogram"), ("lb", "Pound")]
        field = MeasurementFormField(
            measurement=Weight, unit_choices=custom_choices
        )

        self.assertEqual(field.fields[1].choices, custom_choices)

    def test_init_with_min_value_validator(self):
        min_weight = Weight(kg=1.0)
        field = MeasurementFormField(measurement=Weight, min_value=min_weight)

        validators = field.validators
        self.assertTrue(
            any(
                validator.__class__.__name__ == "MinValueValidator"
                for validator in validators
            )
        )

    def test_init_with_max_value_validator(self):
        max_weight = Weight(kg=100.0)
        field = MeasurementFormField(measurement=Weight, max_value=max_weight)

        validators = field.validators
        self.assertTrue(
            any(
                validator.__class__.__name__ == "MaxValueValidator"
                for validator in validators
            )
        )

    def test_init_with_invalid_min_value_type(self):
        with self.assertRaises(ValueError) as cm:
            MeasurementFormField(measurement=Weight, min_value=10.0)

        self.assertIn('"min_value" must be a measure', str(cm.exception))

    def test_init_with_invalid_max_value_type(self):
        with self.assertRaises(ValueError) as cm:
            MeasurementFormField(measurement=Weight, max_value="100kg")

        self.assertIn('"max_value" must be a measure', str(cm.exception))

    def test_init_with_custom_validators(self):
        custom_validators = [lambda x: None]
        field = MeasurementFormField(
            measurement=Weight, validators=custom_validators
        )

        self.assertIn(custom_validators[0], field.validators)

    def test_compress_with_none_data(self):
        field = MeasurementFormField(measurement=Weight)
        result = field.compress(None)
        self.assertIsNone(result)

    def test_compress_with_empty_data(self):
        field = MeasurementFormField(measurement=Weight)
        result = field.compress([])
        self.assertIsNone(result)

    def test_compress_with_empty_value(self):
        field = MeasurementFormField(measurement=Weight)
        result = field.compress([None, "kg"])
        self.assertIsNone(result)

        result = field.compress(["", "kg"])
        self.assertIsNone(result)

    @patch("core.forms.measurement.get_measurement")
    def test_compress_with_valid_data(self, mock_get_measurement):
        field = MeasurementFormField(measurement=Weight)
        mock_weight = Weight(kg=10.0)
        mock_get_measurement.return_value = mock_weight

        result = field.compress([10.0, "kg"])

        mock_get_measurement.assert_called_once_with(Weight, 10.0, "kg")
        self.assertEqual(result, mock_weight)

    def test_widget_configuration(self):
        field = MeasurementFormField(measurement=Weight)

        self.assertIsInstance(field.widget, MeasurementWidget)
        self.assertEqual(len(field.widget.widgets), 2)

    def test_field_inheritance(self):
        field = MeasurementFormField(measurement=Weight)

        self.assertIsInstance(field, forms.MultiValueField)
        self.assertTrue(hasattr(field, "fields"))
        self.assertTrue(hasattr(field, "widget"))

    def test_auto_generated_unit_choices_regular_measurement(self):
        field = MeasurementFormField(measurement=Weight)

        choices = field.fields[1].choices
        choice_values = [choice[0] for choice in choices]
        self.assertIn("g", choice_values)
        self.assertIn("kg", choice_values)

    def test_auto_generated_unit_choices_bidimensional_measurement(self):
        field = MeasurementFormField(measurement=Speed)

        choices = field.fields[1].choices
        choice_values = [choice[0] for choice in choices]
        self.assertTrue(any("__" in choice for choice in choice_values))

    def test_field_attributes_inheritance(self):
        field = MeasurementFormField(
            measurement=Weight, required=False, help_text="Enter weight"
        )

        self.assertFalse(field.required)
        self.assertEqual(field.help_text, "Enter weight")

    @patch("core.forms.measurement.get_measurement")
    def test_compress_with_bidimensional_measurement(
        self, mock_get_measurement
    ):
        field = MeasurementFormField(measurement=Speed)
        mock_speed = Speed(kph=60.0)
        mock_get_measurement.return_value = mock_speed

        result = field.compress([60.0, "km__hr"])

        mock_get_measurement.assert_called_once_with(Speed, 60.0, "km__hr")
        self.assertEqual(result, mock_speed)

    def test_error_message_with_invalid_measurement_subclass(self):
        with self.assertRaises(ValueError) as cm:
            MeasurementFormField(measurement=dict)

        expected_msg = "{} must be a subclass of MeasureBase".format(dict)
        self.assertEqual(str(cm.exception), expected_msg)


class TestMeasurementFormFieldIntegration(TestCase):
    def test_weight_field_integration(self):
        field = MeasurementFormField(measurement=Weight)

        result = field.compress([25.5, "kg"])
        self.assertIsInstance(result, Weight)
        self.assertEqual(result.kg, 25.5)

    def test_distance_field_integration(self):
        field = MeasurementFormField(measurement=Distance)

        result = field.compress([1000.0, "m"])
        self.assertIsInstance(result, Distance)
        self.assertEqual(result.m, 1000.0)

    def test_speed_field_integration(self):
        field = MeasurementFormField(measurement=Speed)

        result = field.compress([100.0, "km__hr"])
        self.assertIsInstance(result, Speed)

    def test_widget_decompress_with_real_weight(self):
        field = MeasurementFormField(measurement=Weight)
        weight = Weight(kg=50.0)

        result = field.widget.decompress(weight)
        self.assertEqual(result[0], 50000.0)
        self.assertEqual(result[1], "g")

    def test_field_validation_with_min_max(self):
        min_weight = Weight(kg=1.0)
        max_weight = Weight(kg=100.0)

        field = MeasurementFormField(
            measurement=Weight, min_value=min_weight, max_value=max_weight
        )

        result = field.compress([50.0, "kg"])
        self.assertIsInstance(result, Weight)

        self.assertEqual(
            len(
                [
                    v
                    for v in field.validators
                    if "Validator" in v.__class__.__name__
                ]
            ),
            2,
        )


class TestMeasurementFormFieldEdgeCases(TestCase):
    def test_compress_with_malformed_data_list(self):
        field = MeasurementFormField(measurement=Weight)

        with self.assertRaises(ValueError):
            field.compress([10.0])

        with self.assertRaises(ValueError):
            field.compress([10.0, "kg", "extra"])

    def test_widget_decompress_string_edge_cases(self):
        unit_choices = [("kg", "Kilogram"), ("g", "Gram")]
        widget = MeasurementWidget(unit_choices=unit_choices)

        with self.assertRaises(ValueError):
            widget.decompress("  10.5   kg  ")

        with self.assertRaises(ValueError):
            widget.decompress("abc kg")

    def test_field_with_all_options(self):
        min_weight = Weight(kg=0.1)
        max_weight = Weight(kg=1000.0)
        custom_choices = [("kg", "Kilogram"), ("g", "Gram")]
        custom_validators = []

        field = MeasurementFormField(
            measurement=Weight,
            min_value=min_weight,
            max_value=max_weight,
            unit_choices=custom_choices,
            validators=custom_validators,
            required=False,
            help_text="Test field",
        )

        self.assertEqual(field.measurement, Weight)
        self.assertEqual(field.fields[1].choices, custom_choices)
        self.assertFalse(field.required)
        self.assertEqual(field.help_text, "Test field")

    def test_bidimensional_separator_in_choices(self):
        field = MeasurementFormField(
            measurement=Speed, bidimensional_separator=" / "
        )

        choices = field.fields[1].choices
        self.assertTrue(any(" / " in choice[1] for choice in choices))


class TestMeasurementWidgetEdgeCases(TestCase):
    def test_decompress_with_ast_literal_eval_edge_cases(self):
        widget = MeasurementWidget(unit_choices=[("kg", "Kilogram")])

        result = widget.decompress("(15.5, 'kg')")
        self.assertEqual(result, [15.5, "kg"])

        result = widget.decompress("[20.0, 'g']")
        self.assertEqual(result, [20.0, "g"])

        result = widget.decompress("25.0 lb")
        self.assertEqual(result, [25.0, "lb"])

    def test_decompress_measure_base_with_no_standard_unit_in_choices(self):
        limited_choices = [("lb", "Pound")]
        widget = MeasurementWidget(unit_choices=limited_choices)

        weight = Weight(kg=1.0)
        result = widget.decompress(weight)

        self.assertAlmostEqual(result[0], 2.20462, places=4)
        self.assertEqual(result[1], "lb")

    def test_widget_attrs_passing(self):
        attrs = {"class": "test-class", "data-test": "value"}
        unit_choices = [("kg", "Kilogram")]

        widget = MeasurementWidget(attrs=attrs, unit_choices=unit_choices)

        self.assertIsInstance(widget.widgets[0], forms.TextInput)
        self.assertIsInstance(widget.widgets[1], forms.Select)
