"""Serializer fields shared across apps."""

import json

from django.utils.translation import gettext_lazy as _
from parler_rest.fields import TranslatedFieldsField
from rest_framework import serializers

from core.utils.i18n import available_language_codes


class TranslatedFieldExtended(TranslatedFieldsField):
    def to_representation(self, value):
        """Convert null values to empty strings in translations."""
        result = super().to_representation(value)
        if isinstance(result, dict):
            for fields in result.values():
                if isinstance(fields, dict):
                    for field_name, field_value in fields.items():
                        if field_value is None:
                            fields[field_name] = ""
        return result

    def to_internal_value(self, data):
        if data is None:
            return {}
        if isinstance(data, str):
            # Multipart bodies carry `translations` as a JSON string, so
            # this parse is on the customer-reachable path (a blog
            # comment, for one). Unguarded, `translations=not-json{`
            # raised `json.JSONDecodeError` out of the serializer and the
            # request answered HTTP 500 — verified.
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                self.fail("invalid")
        if not isinstance(data, dict):
            self.fail("invalid")
        if not self.allow_empty and len(data) == 0:
            self.fail("empty")

        # Neither this override nor parler-rest's original checked the
        # KEYS, and they are caller-supplied. Two things followed, both
        # verified against `POST /api/v1/blog/comment`:
        # `{"xx": {...}}` was accepted with 201 and stored a translation
        # row no reader will ever surface (every read path asks for el,
        # en or de), and a code longer than the column raised
        # `DataError: value too long for type character varying(15)` —
        # another 500 from a request any signed-in customer can send.
        supported = available_language_codes()
        result, errors = {}, {}
        for lang_code, model_fields in data.items():
            if lang_code not in supported:
                errors[lang_code] = [
                    _("Unsupported language code. Expected one of: %(codes)s.")
                    % {"codes": ", ".join(sorted(supported))}
                ]
                continue
            serializer = self.serializer_class(data=model_fields)
            if serializer.is_valid():
                result[lang_code] = serializer.validated_data
            else:
                errors[lang_code] = serializer.errors

        if errors:
            raise serializers.ValidationError(errors)
        return result
