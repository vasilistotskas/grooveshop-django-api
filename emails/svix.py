"""Verifying a Svix-signed webhook (Resend signs its deliveries this way).

The signed content is ``"{svix-id}.{svix-timestamp}.{raw body}"``, signed
with HMAC-SHA256 under the endpoint's secret (``whsec_`` plus base64). The
``svix-signature`` header holds one or more space-separated ``v1,<base64>``
entries (several while a secret is being rotated); one matching is enough.
A timestamp outside the tolerance is refused, which is what stops a captured
delivery from being replayed later.

Hand-written because the whole scheme is these few lines and the library
would be a new dependency for them; ``tests/unit/emails/test_svix.py`` pins a
signature produced by the ``svix`` package.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import time
from collections.abc import Mapping

SECRET_PREFIX = "whsec_"
SIGNATURE_VERSION = "v1"


class InvalidSignature(Exception):
    """The request is not a fresh, correctly signed delivery."""


def _key(secret: str) -> bytes:
    encoded = secret.removeprefix(SECRET_PREFIX)
    try:
        return base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        raise InvalidSignature("The signing secret is malformed.") from error


def sign(secret: str, message_id: str, timestamp: int, body: bytes) -> str:
    """The ``v1,<signature>`` entry for *body*; what a sender emits."""
    signed = f"{message_id}.{timestamp}.".encode() + body
    digest = hmac.new(_key(secret), signed, hashlib.sha256).digest()
    return f"{SIGNATURE_VERSION},{base64.b64encode(digest).decode()}"


def verify(
    secret: str,
    headers: Mapping[str, str],
    body: bytes,
    *,
    tolerance_seconds: int,
    now: float | None = None,
) -> str:
    """Return the verified ``svix-id``; raise :class:`InvalidSignature`.

    *body* is the raw request body, byte for byte: a parsed and re-encoded
    copy would not match.
    """
    message_id = headers.get("svix-id", "")
    timestamp = headers.get("svix-timestamp", "")
    signatures = headers.get("svix-signature", "")
    if not (message_id and timestamp and signatures):
        raise InvalidSignature("A Svix header is missing.")
    try:
        sent_at = int(timestamp)
    except ValueError as error:
        raise InvalidSignature("The timestamp is not a number.") from error
    if abs((time.time() if now is None else now) - sent_at) > tolerance_seconds:
        raise InvalidSignature("The timestamp is outside the tolerance.")
    expected = sign(secret, message_id, sent_at, body)
    for entry in signatures.split():
        if hmac.compare_digest(entry, expected):
            return message_id
    raise InvalidSignature("No signature matches.")
