"""The model field for a URL: one a WHATWG URL parser reads.

Django's ``URLField`` validates with ``URLValidator``, which accepts a
port past 65535. The storefront parses every response with Zod's
``z.url()``, which refuses it, so one such value saved would fail the
storefront's parse, with a 422, on every response that carries it.
``WebURLField`` validates with ``WebURLValidator`` instead, and every
model URL is one (``tests/unit/core/test_url_fields_are_web_urls.py``).
Its serializer field is ``core.api.url_field.WebURLField``, mapped for
every ``ModelSerializer`` in ``CoreConfig.ready``.
"""

from django.db import models

from core.validators.url import WebURLValidator


class WebURLField(models.URLField):
    default_validators = [WebURLValidator()]
