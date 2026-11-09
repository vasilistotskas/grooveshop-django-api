"""The API's URL field: what a WHATWG URL parser reads.

DRF's ``URLField`` validates with Django's ``URLValidator``, which
accepts a port past 65535 that the storefront's ``z.url()`` refuses.
``WebURLField`` validates with ``WebURLValidator`` in its place. Declared
serializer fields use it directly, and ``CoreConfig.ready`` maps the
model's ``core.fields.url.WebURLField`` to it for every
``ModelSerializer``.
"""

from typing import Any

from django.core.validators import URLValidator
from rest_framework import serializers

from core.validators.url import WebURLValidator


class WebURLField(serializers.URLField):
    """One URL check per value, so a bad URL gets one error.

    DRF's ``URLField`` appends a plain ``URLValidator``, which
    ``WebURLValidator`` already runs first: that one is replaced, and a
    ``WebURLValidator`` a caller passes (one narrowed to ``http``/``https``)
    stands in for the default.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.validators = [
            validator
            for validator in self.validators
            if type(validator) is not URLValidator
        ]
        if not any(isinstance(v, WebURLValidator) for v in self.validators):
            self.validators.append(
                WebURLValidator(message=self.error_messages["invalid"])
            )
