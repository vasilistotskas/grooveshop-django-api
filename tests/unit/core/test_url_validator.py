"""``WebURLValidator``: Django's URL check, and a port a URL parser reads.

The storefront parses every response with Zod's ``z.url()`` (WHATWG URL
parsing), so a URL Django saves but that parser refuses fails every
response that carries it with a 422.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from core.validators.url import WebURLValidator

validate = WebURLValidator(schemes=["http", "https"])


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "https://example.com/path?q=1#top",
        "https://example.com:8443/x",
        "https://example.com:65535",
        "http://[::1]:8080/",
    ],
)
def test_accepts_a_web_url(url):
    validate(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com:65536",
        "https://example.com:99999/x",
        "ftp://example.com",
        "not a url",
    ],
)
def test_refuses_what_a_url_parser_refuses(url):
    with pytest.raises(ValidationError) as caught:
        validate(url)

    assert caught.value.code == "invalid"
