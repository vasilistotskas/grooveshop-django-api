import json

from django.test import TestCase
from rest_framework import serializers

from core.utils.serializers import flatten_dict_for_form_data
from core.utils.serializers import MultiSerializerMixin
from core.utils.serializers import TranslatedFieldExtended


class DummySerializer(serializers.Serializer):
    field1 = serializers.CharField()
    field2 = serializers.IntegerField()


class AnotherDummySerializer(serializers.Serializer):
    field3 = serializers.CharField()
    field4 = serializers.BooleanField()


class TestTranslatedFieldExtended(TestCase):
    def test_to_internal_value_with_valid_data(self):
        data = {
            "en": {"field1": "value1", "field2": 123},
            "fr": {"field1": "valeur1", "field2": 456},
        }
        field = TranslatedFieldExtended(serializer_class=DummySerializer)
        result = field.to_internal_value(json.dumps(data))
        expected_result = {
            "en": {"field1": "value1", "field2": 123},
            "fr": {"field1": "valeur1", "field2": 456},
        }
        self.assertEqual(result, expected_result)


class TestFlattenDictForFormData(TestCase):
    def test_flatten_dict_for_form_data(self):
        input_dict = {
            "field1": "value1",
            "field2": {"subfield1": "subvalue1", "subfield2": "subvalue2"},
            "field3": [1, 2, 3],
            "field4": [{"subfield": "subvalue1"}, {"subfield": "subvalue2"}],
        }
        result = flatten_dict_for_form_data(input_dict)
        expected_result = {
            "field1": "value1",
            "field2.subfield1": "subvalue1",
            "field2.subfield2": "subvalue2",
            "field3[0]": 1,
            "field3[1]": 2,
            "field3[2]": 3,
            "field4[0]subfield": "subvalue1",
            "field4[1]subfield": "subvalue2",
        }
        self.assertDictEqual(result, expected_result)


class TestMultiSerializerMixin(TestCase):
    def test_get_serializer_class_with_default_action(self):
        mixin = MultiSerializerMixin()
        mixin.action = "unknown_action"
        serializer_class = mixin.get_serializer_class()
        self.assertIsNone(serializer_class)
