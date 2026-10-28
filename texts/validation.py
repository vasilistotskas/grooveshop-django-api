"""Whether a store may replace a text with a value.

Three checks, cheapest first, then the storefront (the authority on the
message syntax and on which keys exist and are locked). Every refusal is
a Django ``ValidationError`` with a stable ``code`` (the codes are
identifiers, never messages: ``docs/error-contract.md``) and a message
in the admin's language. The storefront's own sentence is English and
travels in ``params["detail"]`` as a hint.
"""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from texts import storefront

VALUE_BLANK = "value_blank"
VALUE_TOO_LONG = "value_too_long"
STOREFRONT_UNAVAILABLE = "storefront_unavailable"
UNSUPPORTED_LANGUAGE = "unsupported_language"

#: The message each code shows. Lazy, so the admin's language wins.
MESSAGES = {
    storefront.UNKNOWN_KEY: _(
        "This text no longer exists in the storefront, so it cannot be edited."
    ),
    storefront.LOCKED_KEY: _(
        "This text is managed by the platform and cannot be changed."
    ),
    storefront.SYNTAX_ERROR: _(
        "The text is not valid. Check the braces { }, the | separators and "
        "the @ references."
    ),
    storefront.PLACEHOLDER_MISMATCH: _(
        "The text must keep the same placeholders as the original."
    ),
    VALUE_BLANK: _("Enter a text, or reset it to the default."),
    VALUE_TOO_LONG: _(
        "The text is too long: %(length)s of %(max)s characters."
    ),
    STOREFRONT_UNAVAILABLE: _(
        "The storefront could not be reached to check this text, so nothing "
        "was saved. Try again in a moment."
    ),
    UNSUPPORTED_LANGUAGE: _("The storefront has no language %(language)s."),
}


def refusal(code: str, **params: object) -> ValidationError:
    return ValidationError(MESSAGES[code], code=code, params=params)


def normalise(value: str) -> str:
    """What is stored: browsers post line breaks as CRLF."""
    return value.replace("\r\n", "\n").strip()


def check_value(
    *,
    namespace: str,
    key: str,
    language: str,
    value: str,
    schema: str,
) -> str:
    """The value to store, or a ``ValidationError``.

    Raises ``ValidationError`` with ``storefront_unavailable`` when the
    storefront cannot answer: an unchecked text is never saved.
    """
    value = normalise(value)
    if not value:
        raise refusal(VALUE_BLANK)
    maximum = settings.TEXTS_MAX_VALUE_LENGTH
    if len(value) > maximum:
        raise refusal(VALUE_TOO_LONG, length=len(value), max=maximum)
    try:
        rejection = storefront.validate_text(
            namespace=namespace,
            key=key,
            language=language,
            value=value,
            schema=schema,
        )
    except storefront.StorefrontUnavailable as exc:
        raise refusal(STOREFRONT_UNAVAILABLE) from exc
    if rejection is not None:
        raise refusal(rejection.code, detail=rejection.detail)
    return value
