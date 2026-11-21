"""snake_case <-> camelCase for one name: the one implementation.

The API's bodies are converted by djangorestframework-camel-case (its
renderer, parsers and middleware, ``settings.api``). Everything else that
converts a single name - a query parameter, an ordering key, a JSON
schema's property names - goes through these two functions, which are that
library's own rules, so a name always reads back as the field it came
from (``alpha_2`` -> ``alpha2`` -> ``alpha_2``).
"""

from __future__ import annotations

from djangorestframework_camel_case.util import (
    camel_to_underscore,
    camelize_re,
    underscore_to_camel,
)


def snake_to_camel(snake_str: str) -> str:
    """``user_name`` -> ``userName``, ``alpha_2`` -> ``alpha2``."""
    return camelize_re.sub(underscore_to_camel, snake_str)


def camel_to_snake(camel_str: str) -> str:
    """``userName`` -> ``user_name``, ``alpha2`` -> ``alpha_2``."""
    return camel_to_underscore(camel_str)
