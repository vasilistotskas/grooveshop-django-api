"""Opting out of marketing mail.

A recipient is identified by a keyed hash of their address, never by the
address, so the opt-out list holds none. Only marketing types honour a row:
a receipt or a security mail is not something a person can decline.

The unsubscribe link carries a signed token naming the owning schema, the
address hash and the type (or ``*`` for every marketing type). It is signed
under a salt of its own, so it cannot be replayed as another kind of token,
and it names the schema because ``SECRET_KEY`` is global: a token minted for
one store must not verify on another's host (the same rule as
``user.utils.subscription._make_unsubscribe_token``).
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.core import signing
from django.db import connection

from core.utils.tenant_urls import get_tenant_api_base_url
from emails.models import EVERY_MARKETING_TYPE, EmailOptOut
from emails.registry import UnknownEmailType, registry
from emails.types import EmailType

TOKEN_SALT = "emails.optout"
#: A link in a mail that sits in an inbox for months must still work
#: (RFC 8058 one-click), while a leaked token stays bounded.
TOKEN_MAX_AGE = timedelta(days=365)
#: The unsubscribe endpoint, on the API host: it has no storefront proxy.
UNSUBSCRIBE_PATH = "/api/v1/emails/unsubscribe"


class InvalidToken(Exception):
    """The token is forged, expired, for another store or for a type that
    no longer takes opt-outs."""


def address_hash(address: str) -> str:
    """HMAC-SHA256 of the normalised address under ``EMAIL_HASH_KEY``."""
    return hmac.new(
        settings.EMAIL_HASH_KEY.encode(),
        address.strip().lower().encode(),
        hashlib.sha256,
    ).hexdigest()


def _takes_opt_out(type_key: str) -> bool:
    return (
        type_key == EVERY_MARKETING_TYPE
        or registry.get(type_key).opt_out_applies
    )


def is_opted_out(address: str, email_type: EmailType) -> bool:
    """Whether *address* declined this type, or every marketing type. Never
    true for a type that takes no opt-out."""
    if not email_type.opt_out_applies:
        return False
    return EmailOptOut.objects.filter(
        address_hash=address_hash(address),
        type_key__in=[email_type.key, EVERY_MARKETING_TYPE],
    ).exists()


def _record(
    hashed: str, type_key: str, source: EmailOptOut.Source
) -> EmailOptOut:
    row, _created = EmailOptOut.objects.get_or_create(
        address_hash=hashed, type_key=type_key, defaults={"source": source}
    )
    return row


def opt_out(
    address: str, type_key: str, *, source: EmailOptOut.Source
) -> EmailOptOut:
    """Record the opt-out (once). Raises ``UnknownEmailType`` for an
    unknown type and ``ValueError`` for one that takes no opt-out."""
    if not _takes_opt_out(type_key):
        raise ValueError(f"Email type {type_key!r} takes no opt-out.")
    return _record(address_hash(address), type_key, source)


def make_token(address: str, type_key: str) -> str:
    return signing.dumps(
        {
            "schema": connection.schema_name,
            "h": address_hash(address),
            "t": type_key,
        },
        salt=TOKEN_SALT,
    )


def unsubscribe_url(address: str, email_type: EmailType) -> str:
    return (
        f"{get_tenant_api_base_url()}{UNSUBSCRIBE_PATH}/"
        f"{make_token(address, email_type.key)}"
    )


@dataclass(frozen=True, slots=True)
class TokenClaims:
    address_hash: str
    type_key: str


def read_token(token: str) -> TokenClaims:
    """What a token carries. Raises :class:`InvalidToken`."""
    try:
        claims = signing.loads(token, salt=TOKEN_SALT, max_age=TOKEN_MAX_AGE)
        if claims["schema"] != connection.schema_name:
            raise InvalidToken("The token was minted for another store.")
        if not _takes_opt_out(claims["t"]):
            raise InvalidToken("The type takes no opt-out.")
    except (signing.BadSignature, KeyError, UnknownEmailType) as error:
        raise InvalidToken(str(error)) from error
    return TokenClaims(address_hash=claims["h"], type_key=claims["t"])


def opt_out_by_token(token: str, *, source: EmailOptOut.Source) -> EmailOptOut:
    """Record the opt-out a token carries. Raises :class:`InvalidToken`."""
    claims = read_token(token)
    return _record(claims.address_hash, claims.type_key, source)
