from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.utils.deconstruct import deconstructible


@deconstructible
class WebURLValidator(URLValidator):
    """Django's ``URLValidator``, and a port a URL parser can read.

    ``URLValidator`` matches the port as up to five digits, so it accepts
    ``https://example.com:65536``. The storefront parses every response
    with Zod's ``z.url()`` (WHATWG URL parsing), which refuses it: one
    such link saved would fail the storefront's parse, with a 422, on
    every page whose response carries it. ``urlsplit`` raises the same
    way past 65535.
    """

    def __call__(self, value):
        super().__call__(value)
        try:
            # Reading the port parses it: ValueError past 65535.
            _port = urlsplit(value).port
        except ValueError as error:
            raise ValidationError(
                self.message, code=self.code, params={"value": value}
            ) from error
