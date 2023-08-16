import json

from django.core.exceptions import ImproperlyConfigured
from parler_rest.fields import TranslatedFieldsField
from rest_framework import serializers


class TranslatedFieldExtended(TranslatedFieldsField):
    def to_internal_value(self, data) -> dict:
        if data is None:
            return {}
        if isinstance(data, str):
            data = json.loads(data)
        if not isinstance(data, dict):
            self.fail("invalid")
        if not self.allow_empty and len(data) == 0:
            self.fail("empty")

        result, errors = {}, {}
        for lang_code, model_fields in data.items():
            serializer = self.serializer_class(data=model_fields)
            if serializer.is_valid():
                result[lang_code] = serializer.validated_data
            else:
                errors[lang_code] = serializer.errors

        if errors:
            raise serializers.ValidationError(errors)
        return result


def flatten_dict_for_form_data(input_dict: dict, sep: str = "[{i}]") -> dict:
    def __flatten(
        value: any, prefix: str, result_dict: dict, previous: str = ""
    ) -> dict:
        if isinstance(value, dict):
            if previous == "dict":
                prefix += "."

            for key, v in value.items():
                __flatten(v, prefix + key, result_dict, "dict")

        elif isinstance(value, (list, tuple)):
            for i, v in enumerate(value):
                __flatten(v, prefix + sep.format(i=i), result_dict)
        else:
            result_dict[prefix] = value

        return result_dict

    return __flatten(input_dict, "", {})


class MultiSerializerMixin:
    action = None
    serializers = {
        "default": None,
        "list": None,
        "create": None,
        "retrieve": None,
        "update": None,
        "partial_update": None,
        "destroy": None,
    }

    def get_serializer_class(self):
        if not hasattr(self, "serializers"):
            raise ImproperlyConfigured(
                "%(cls)s is missing the serializer_classes attribute. Define "
                "%(cls)s.serializer_classes, or override "
                "%(cls)s.get_serializer_class()." % {"cls": self.__class__.__name__}
            )
        return self.serializers.get(self.action, self.serializers.get("default"))
