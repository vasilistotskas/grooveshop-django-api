"""Remove known values (a person's email, name, phone, the text they typed)
from free text that copied them, for GDPR erasure (``order.privacy`` and
the carriers' ``erase_recipient_data``).
"""

from __future__ import annotations

from typing import Any

REDACTED = "[deleted]"
# Shorter values (a two-letter name) would match unrelated text.
MIN_REDACTED_LENGTH = 6
# A copy cut at its column's length ends with the start of the value; one
# this short is never cut there, so it is only matched whole.
_MIN_CUT_TEXT = 32


def redaction_texts(*values: object) -> list[str]:
    """The *values* worth searching for, longest first (a name inside a
    longer reason goes with the reason)."""
    texts = {
        str(value)
        for value in values
        if value and len(str(value)) >= MIN_REDACTED_LENGTH
    }
    return sorted(texts, key=len, reverse=True)


def redact(value: Any, texts: list[str]) -> Any:
    """*value* (a string, or JSON holding strings) with each of *texts*
    replaced by ``REDACTED``, also where a column's length cut one short.
    Keys of JSON objects are left alone."""
    if isinstance(value, dict):
        return {key: redact(item, texts) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item, texts) for item in value]
    if not isinstance(value, str):
        return value
    for text in texts:
        value = _without(value, text)
    return value


def _without(value: str, text: str) -> str:
    if text in value:
        return value.replace(text, REDACTED)
    for length in range(len(text) - 1, _MIN_CUT_TEXT - 1, -1):
        if value.endswith(text[:length]):
            return value[:-length] + REDACTED
    return value
