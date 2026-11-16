"""Query-string parsing and the allowlists for the search endpoints.

Everything here reads input from external callers: a value that does not
parse is a 400, never a 500, and a field that is not in an allowlist is
dropped so it cannot reach the Meilisearch query DSL.
"""

from __future__ import annotations

from django.conf import settings as django_settings
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import ValidationError

from core.store_settings import store_setting
from product.attribute_selection import ATTRIBUTE_VALUES_FIELD

VALID_LANGUAGE_CODES = frozenset(
    code for code, _name in django_settings.LANGUAGES
)

# Allowlists for sort fields and facets accepted from external callers.
# Reject anything not in these sets to prevent filter-injection into
# the Meilisearch query DSL.
# Map the camelCase sort values accepted from external callers (the API
# convention) to the snake_case field names the Meilisearch index actually
# exposes as sortableAttributes. Anything not in this map is rejected to
# prevent filter-injection into the query DSL. Previously the allowlist held
# the camelCase names and they were passed straight to order_by, so sorting
# silently did nothing — the index only knows the snake_case names.
PRODUCT_SORT_FIELD_MAP: dict[str, str] = {
    "effectivePrice": "effective_price",
    "-effectivePrice": "-effective_price",
    "likesCount": "likes_count",
    "-likesCount": "-likes_count",
    "viewCount": "view_count",
    "-viewCount": "-view_count",
    "createdAt": "created_at",
    "-createdAt": "-created_at",
}
ALLOWED_PRODUCT_FACETS: frozenset[str] = frozenset(
    {
        "category",
        "brand",
        "price_range",
        "effective_price",
        "language_code",
        "likes_count",
        "view_count",
        ATTRIBUTE_VALUES_FIELD,
    }
)


def get_max_search_limit() -> int:
    return store_setting("SEARCH_MAX_LIMIT")


def parse_int(value: str | None, default: int, name: str) -> int:
    if value is None:
        return default
    try:
        return max(0, int(value))
    except ValueError, TypeError:
        raise ValidationError(
            {name: _("Must be a valid integer.")}, code="invalid_integer"
        )


def parse_optional_int(value: str | None, name: str) -> int | None:
    """Parse an optional integer filter, raising a 400 (not a 500) on garbage."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError, TypeError:
        raise ValidationError(
            {name: _("Must be a valid integer.")}, code="invalid_integer"
        )


def parse_optional_float(value: str | None, name: str) -> float | None:
    """Parse an optional numeric filter, raising a 400 (not a 500) on garbage."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError, TypeError:
        raise ValidationError(
            {name: _("Must be a valid number.")}, code="invalid_number"
        )


_TRUE_VALUES = frozenset({"true", "1"})
_FALSE_VALUES = frozenset({"false", "0"})


def parse_bool_flag(value: str | None, name: str) -> bool:
    """Parse an optional on/off filter; absent or false both mean "no filter".

    400 (not 500, not silently ignored) on anything but true/false/1/0, so
    a typo'd ``inStock=ture`` cannot return unfiltered results as if it had
    been applied.
    """
    if value is None or value == "":
        return False
    lowered = value.strip().lower()
    if lowered in _TRUE_VALUES:
        return True
    if lowered in _FALSE_VALUES:
        return False
    raise ValidationError(
        {name: _("Must be true or false.")}, code="invalid_boolean"
    )


def validate_language_code(language_code: str | None) -> str | None:
    if language_code and language_code not in VALID_LANGUAGE_CODES:
        raise ValidationError(
            {"language_code": _("Invalid language code.")},
            code="invalid_language",
        )
    return language_code


def relax_query(query: str) -> str | None:
    """Return the query with its leading word dropped, for a retry.

    Meilisearch's default ``last`` matching strategy already relaxes
    trailing words, so the only multi-word shape it can never rescue is
    a leading word that matches nothing (an unindexed transliteration
    convention, a pasted slug fragment, a typo beyond tolerance).
    Returns ``None`` for single-word queries — there is nothing left to
    relax.
    """
    parts = query.split()
    if len(parts) < 2:
        return None
    return " ".join(parts[1:])
