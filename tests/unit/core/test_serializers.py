import decimal

from django.test import TestCase
from measurement.measures import Distance, Temperature, Weight
from rest_framework import serializers

from core.serializers import MeasurementSerializerField


class MeasurementSerializerFieldTest(TestCase):
    """Tests for the MeasurementSerializerField class."""

    def setUp(self):
        """Set up test data."""
        self.weight_measurement_field = MeasurementSerializerField(
            measurement=Weight
        )
        self.distance_measurement_field = MeasurementSerializerField(
            measurement=Distance
        )
        self.temperature_measurement_field = MeasurementSerializerField(
            measurement=Temperature
        )

        # Create sample measurement objects
        self.weight = Weight(kg=10)
        self.distance = Distance(km=5)
        self.temperature = Temperature(c=25)

    def test_initialization(self):
        """Test that the field is initialized correctly."""
        self.assertEqual(self.weight_measurement_field.measurement, Weight)
        self.assertEqual(self.distance_measurement_field.measurement, Distance)
        self.assertEqual(
            self.temperature_measurement_field.measurement, Temperature
        )

    def test_to_representation_with_measurement_object(self):
        """Test to_representation with a measurement object."""
        result = self.weight_measurement_field.to_representation(self.weight)
        self.assertEqual(result, {"unit": "kg", "value": 10.0})

        result = self.distance_measurement_field.to_representation(
            self.distance
        )
        self.assertEqual(result, {"unit": "km", "value": 5.0})

        result = self.temperature_measurement_field.to_representation(
            self.temperature
        )
        self.assertEqual(result, {"unit": "c", "value": 25.0})

    def test_to_representation_with_decimal(self):
        """Test to_representation with a decimal value."""
        result = self.weight_measurement_field.to_representation(
            decimal.Decimal("15.5")
        )
        # The implementation uses 'unknown' as the unit for non-measurement objects
        self.assertEqual(result, {"unit": "g", "value": 15.5})

    def test_to_representation_with_float(self):
        """Test to_representation with a float value."""
        result = self.distance_measurement_field.to_representation(7.5)
        # The implementation uses 'unknown' as the unit for non-measurement objects
        self.assertEqual(result, {"unit": "m", "value": 7.5})

    def test_to_representation_with_int(self):
        """Test to_representation with an integer value."""
        result = self.temperature_measurement_field.to_representation(30)
        # The implementation uses 'unknown' as the unit for non-measurement objects
        self.assertEqual(result, {"unit": "k", "value": 30.0})

    def test_to_representation_with_other_object(self):
        """Test to_representation with an object that doesn't match other cases."""

        class CustomObject:
            def __str__(self):
                return "custom_value"

        obj = CustomObject()
        result = self.weight_measurement_field.to_representation(obj)
        self.assertEqual(result, {"unit": "unknown", "value": "custom_value"})

    def test_to_internal_value_with_valid_data(self):
        """Test to_internal_value with valid data."""
        # Test with Weight
        data = {"unit": "kg", "value": "10.5"}
        result = self.weight_measurement_field.to_internal_value(data)
        self.assertIsInstance(result, Weight)
        self.assertEqual(result.kg, 10.5)

        # Test with Distance
        data = {"unit": "mi", "value": "3.1"}
        result = self.distance_measurement_field.to_internal_value(data)
        self.assertIsInstance(result, Distance)
        self.assertEqual(result.mi, 3.1)

        # Test with Temperature
        data = {"unit": "f", "value": "98.6"}
        result = self.temperature_measurement_field.to_internal_value(data)
        self.assertIsInstance(result, Temperature)
        self.assertEqual(result.f, 98.6)

    def test_to_internal_value_with_missing_keys(self):
        """Test to_internal_value with missing keys."""
        # Missing 'unit'
        with self.assertRaises(serializers.ValidationError) as cm:
            self.weight_measurement_field.to_internal_value({"value": "10"})
        # Check that it's the correct error message
        self.assertEqual(cm.exception.detail[0].code, "missing_keys")

        # Missing 'value'
        with self.assertRaises(serializers.ValidationError) as cm:
            self.weight_measurement_field.to_internal_value({"unit": "kg"})
        self.assertEqual(cm.exception.detail[0].code, "missing_keys")

        # Not a dict
        with self.assertRaises(serializers.ValidationError) as cm:
            self.weight_measurement_field.to_internal_value("not a dict")
        self.assertEqual(cm.exception.detail[0].code, "missing_keys")

    def test_to_internal_value_with_invalid_unit(self):
        """Test to_internal_value with an invalid unit."""
        with self.assertRaises(serializers.ValidationError) as cm:
            self.weight_measurement_field.to_internal_value(
                {"unit": "invalid_unit", "value": "10"}
            )
        self.assertEqual(cm.exception.detail[0].code, "invalid_unit")

    def test_to_internal_value_with_invalid_value(self):
        """Test to_internal_value with an invalid decimal value."""
        with self.assertRaises(serializers.ValidationError) as cm:
            self.weight_measurement_field.to_internal_value(
                {"unit": "kg", "value": "not a number"}
            )
        self.assertEqual(cm.exception.detail[0].code, "invalid_value")

    def test_bidimensional_measure(self):
        """Test with a BidimensionalMeasure like Speed."""
        from measurement.measures import Speed

        speed_field = MeasurementSerializerField(measurement=Speed)

        # Test to_representation
        speed = Speed(kph=100)
        result = speed_field.to_representation(speed)
        # The actual unit for kph in the implementation is 'km__hr'
        self.assertEqual(result, {"unit": "km__hr", "value": 100.0})

        # Test to_internal_value
        data = {"unit": "mph", "value": "60"}
        result = speed_field.to_internal_value(data)
        self.assertIsInstance(result, Speed)
        self.assertAlmostEqual(result.mph, 60.0)

    def test_integration_with_serializer(self):
        """Test integration with a serializer."""

        class SampleModel:
            def __init__(self, weight):
                self.weight = weight

        class SampleSerializer(serializers.Serializer):
            weight = MeasurementSerializerField(measurement=Weight)

        # Test serialization
        instance = SampleModel(weight=Weight(kg=20))
        serializer = SampleSerializer(instance)
        self.assertEqual(
            serializer.data["weight"], {"unit": "kg", "value": 20.0}
        )

        # Test deserialization
        data = {"weight": {"unit": "lb", "value": "44.0924"}}
        serializer = SampleSerializer(data=data)
        self.assertTrue(serializer.is_valid())
        # 44.0924 lb ≈ 20 kg
        self.assertAlmostEqual(
            serializer.validated_data["weight"].kg, 20.0, places=1
        )
