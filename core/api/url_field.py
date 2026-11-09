"""The API's URL field: what a WHATWG URL parser reads.

DRF's ``URLField`` validates with Django's ``URLValidator``, which
accepts a port past 65535 that the storefront's ``z.url()`` refuses.
``WebURLField`` adds ``WebURLValidator``. Declared serializer fields use
it directly, and ``CoreConfig.ready`` maps the model's
``core.fields.url.WebURLField`` to it for every ``ModelSerializer``.
"""

from typing import Any

from rest_framework import serializers

from core.validators.url import WebURLValidator


class WebURLField(serializers.URLField):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.validators.append(
            WebURLValidator(message=self.error_messages["invalid"])
        )
