"""BoxNow webhook signature verification — pure helpers, no Django deps.

This module is intentionally free of Django imports so it can be tested
in complete isolation without a Django setup.

Datasignature spec (from BoxNow Webhook-Based Parcel Tracking Guide v1.4.6):
    The HMAC-SHA256 is computed over the raw ``data`` JSON object exactly as
    received.  Do not reformat, beautify, normalise or otherwise manipulate
    the JSON (including whitespace or key ordering) before generating the hash.
    The result is hex-encoded.
"""

from __future__ import annotations

import hashlib
import hmac


class BoxNowWebhookError(Exception):
    """Raised when the BoxNow webhook envelope is malformed."""


# ---------------------------------------------------------------------------
# Substring extraction
# ---------------------------------------------------------------------------

_DATA_KEY = b'"data"'


def extract_data_substring(raw_body: bytes) -> bytes:
    """Return the bytes that constitute the value of the top-level ``"data"`` key.

    Operates on the raw body bytes without any JSON parse/re-serialise cycle
    so whitespace and key ordering are preserved exactly.

    Algorithm:
        1. Find ``"data"`` in *raw_body*.
        2. Skip optional whitespace, then a ``:``, then optional whitespace.
        3. Expect a ``{`` — locate the opening brace.
        4. Walk forward tracking brace depth and string state (honouring
           ``\\`` escapes inside strings so ``\\"`` does not toggle string mode).
        5. Return the slice from the opening ``{`` to the matching ``}`` (both
           inclusive).

    Args:
        raw_body: The complete, unmodified HTTP request body bytes.

    Returns:
        The raw bytes of the ``data`` object, e.g. ``b'{"parcelId":"..."}'``.

    Raises:
        BoxNowWebhookError: If the ``"data"`` key is not found or the braces
            are unbalanced.
    """
    # Locate the `"data"` key followed by `:` and `{`. The literal substring
    # `"data"` (with surrounding quotes) cannot appear inside `"datacontenttype"`
    # or `"datasignature"` because those keys lack the trailing quote
    # immediately after `data`. It CAN, however, appear verbatim inside a
    # quoted string value in the envelope (e.g. a customer name containing
    # the word "data"). To stay robust against that, we keep advancing past
    # any false-positive match where the `:` / `{` separators don't follow.
    length = len(raw_body)
    search_from = 0

    while True:
        key_pos = raw_body.find(_DATA_KEY, search_from)
        if key_pos == -1:
            raise BoxNowWebhookError(
                'invalid envelope: missing top-level "data" key'
            )

        # Advance past the key token itself.
        pos = key_pos + len(_DATA_KEY)

        # Skip optional whitespace then the colon separator.
        while pos < length and raw_body[pos : pos + 1] in (
            b" ",
            b"\t",
            b"\r",
            b"\n",
        ):
            pos += 1
        if pos >= length or raw_body[pos : pos + 1] != b":":
            search_from = key_pos + 1
            continue
        pos += 1  # consume ':'

        # Skip optional whitespace before the opening brace.
        while pos < length and raw_body[pos : pos + 1] in (
            b" ",
            b"\t",
            b"\r",
            b"\n",
        ):
            pos += 1

        if pos >= length or raw_body[pos : pos + 1] != b"{":
            search_from = key_pos + 1
            continue

        # Found a real top-level `"data": { ... }` entry.
        break

    start = pos  # index of the opening '{'
    depth = 0
    in_string = False

    while pos < length:
        byte = raw_body[pos : pos + 1]

        if in_string:
            if byte == b"\\":
                # Escape sequence — skip the next byte unconditionally so that
                # \" does not toggle string mode.
                pos += 2
                continue
            if byte == b'"':
                in_string = False
        else:
            if byte == b'"':
                in_string = True
            elif byte == b"{":
                depth += 1
            elif byte == b"}":
                depth -= 1
                if depth == 0:
                    # Found the matching closing brace.
                    return raw_body[start : pos + 1]

        pos += 1

    raise BoxNowWebhookError(
        'invalid envelope: unbalanced braces in "data" value'
    )


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------


def verify_signature(
    raw_data_bytes: bytes,
    datasignature_hex: str,
    secret: str,
) -> bool:
    """Verify the BoxNow HMAC-SHA256 datasignature.

    Uses :func:`hmac.compare_digest` for constant-time comparison to prevent
    timing-attack leakage.

    Args:
        raw_data_bytes: The raw bytes of the ``data`` JSON object exactly as
            extracted from the request body (no normalisation).
        datasignature_hex: The hex-encoded HMAC from the ``datasignature``
            envelope field.
        secret: The partner webhook secret (plaintext).

    Returns:
        ``True`` if the signature is valid, ``False`` otherwise.
    """
    # The signature comes off an UNAUTHENTICATED JSON body, so its type
    # is whatever the caller sent. `null`, a number, a list and an
    # object all reached `.encode()` and raised AttributeError before
    # the view's 401 path — a 500 on a public endpoint, from a
    # one-character payload change. A non-string is simply not a valid
    # signature.
    if not isinstance(datasignature_hex, str):
        return False

    expected = hmac.new(
        secret.encode(),
        raw_data_bytes,
        hashlib.sha256,
    ).hexdigest()
    # Bytes, not str: a signature header carrying a non-ASCII
    # character would raise TypeError out of `compare_digest` rather
    # than simply failing verification.
    return hmac.compare_digest(
        expected.encode("ascii"),
        datasignature_hex.encode("utf-8", "surrogateescape"),
    )


# ---------------------------------------------------------------------------
# Envelope validation
# ---------------------------------------------------------------------------

_EXPECTED_SPEC_VERSION = "1.0"
_EXPECTED_EVENT_TYPE = "gr.boxnow.parcel_event_change"


def validate_envelope(envelope: dict) -> None:
    """Validate top-level CloudEvents envelope fields.

    Raises:
        BoxNowWebhookError: If ``specversion`` or ``type`` do not match the
            expected BoxNow values.
    """
    specversion = envelope.get("specversion", "")
    if specversion != _EXPECTED_SPEC_VERSION:
        raise BoxNowWebhookError(
            f"invalid envelope: specversion={specversion!r},"
            f" expected {_EXPECTED_SPEC_VERSION!r}"
        )

    event_type = envelope.get("type", "")
    if event_type != _EXPECTED_EVENT_TYPE:
        raise BoxNowWebhookError(
            f"invalid envelope: type={event_type!r},"
            f" expected {_EXPECTED_EVENT_TYPE!r}"
        )
