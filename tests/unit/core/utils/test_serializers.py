import json

from django.test import TestCase
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from core.utils.serializers import TranslatedFieldExtended


class DummySerializer(serializers.Serializer):
    field1 = serializers.CharField()
    field2 = serializers.IntegerField()


class TestTranslatedFieldExtended(TestCase):
    def test_to_internal_value_with_valid_data(self):
        # ``el`` and ``en``, not the ``fr`` this used to send: the
        # deployment serves el/en, so ``fr`` was only ever accepted
        # because the keys went unchecked.
        data = {
            "en": {"field1": "value1", "field2": 123},
            "el": {"field1": "τιμή1", "field2": 456},
        }
        field = TranslatedFieldExtended(serializer_class=DummySerializer)
        result = field.to_internal_value(json.dumps(data))
        expected_result = {
            "en": {"field1": "value1", "field2": 123},
            "el": {"field1": "τιμή1", "field2": 456},
        }
        self.assertEqual(result, expected_result)

    def test_an_unsupported_language_is_a_validation_error(self):
        field = TranslatedFieldExtended(serializer_class=DummySerializer)

        with self.assertRaises(ValidationError) as ctx:
            field.to_internal_value(
                json.dumps({"fr": {"field1": "valeur1", "field2": 456}})
            )

        self.assertIn("fr", ctx.exception.detail)

    def test_a_multipart_string_that_is_not_json_is_a_validation_error(self):
        """It raised ``json.JSONDecodeError``, i.e. HTTP 500."""
        field = TranslatedFieldExtended(serializer_class=DummySerializer)

        with self.assertRaises(ValidationError):
            field.to_internal_value("not-json{")
