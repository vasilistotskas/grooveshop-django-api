"""
String case conversion utilities.

Provides functions for converting between snake_case and camelCase naming conventions.
"""

from __future__ import annotations

import re

from djangorestframework_camel_case.util import (
    camelize_re,
    underscore_to_camel as underscore_to_camel_callback,
)


def snake_to_camel(snake_str: str) -> str:
    """
    Convert snake_case string to camelCase.

    Args:
        snake_str: String in snake_case format (e.g., 'user_name')

    Returns:
        String in camelCase format (e.g., 'userName')

    Examples:
        >>> snake_to_camel('user_name')
        'userName'
        >>> snake_to_camel('created_at')
        'createdAt'
    """
    return re.sub(camelize_re, underscore_to_camel_callback, snake_str)


def camel_to_snake(camel_str: str) -> str:
    """
    Convert camelCase string to snake_case.

    Args:
        camel_str: String in camelCase format (e.g., 'userName')

    Returns:
        String in snake_case format (e.g., 'user_name')

    Examples:
        >>> camel_to_snake('userName')
        'user_name'
        >>> camel_to_snake('createdAt')
        'created_at'
    """
    # Insert underscore before uppercase letters
    camel_str = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", camel_str)
    camel_str = re.sub("([a-z0-9])([A-Z])", r"\1_\2", camel_str)
    return camel_str.lower()
