"""Single-use tickets: a random key that names a user once.

A client that cannot send its credentials where it is going (a WebSocket
URL, a redirect into another host) first fetches a ticket over an
authenticated request and presents THAT. The ticket is 256 random bits
stored in Redis under the active schema, for a short time, and **spent
atomically**: ``GETDEL`` reads and deletes in one round trip, so of two
requests with one ticket exactly one gets the user and the other nothing,
and an intercepted ticket is worthless after its first use.

Every ticket lives under ``STRICT_NAMESPACE`` (``core.caches``): a miss here
switches a security control off, so an unreachable Redis raises instead of
failing open (a lost ``set`` would hand out a ticket nobody can redeem, a
lost ``GETDEL`` would leave one redeemable). The cache key function puts the
schema in front, so a ticket minted on one store finds nothing on another.

``purpose`` keeps the tickets of different uses apart: a WebSocket ticket
can never be spent as a page builder one.
"""

from __future__ import annotations

import logging
import secrets

from django.core.cache import cache

from core.caches import STRICT_NAMESPACE

logger = logging.getLogger(__name__)

#: The tickets this platform mints. Add a purpose here, never an ad hoc
#: string at a call site.
WEBSOCKET = "ws"
BUILDER_EDITOR = "builder-editor"

PURPOSES = frozenset({WEBSOCKET, BUILDER_EDITOR})


def _key(purpose: str, ticket: str) -> str:
    if purpose not in PURPOSES:
        raise ValueError(f"unknown ticket purpose: {purpose!r}")
    return f"{STRICT_NAMESPACE}ticket:{purpose}:{ticket}"


def mint(purpose: str, user_id: int, ttl_seconds: int) -> str:
    """A new ticket naming *user_id*, valid for *ttl_seconds*."""
    ticket = secrets.token_urlsafe(32)
    cache.set(_key(purpose, ticket), user_id, ttl_seconds)
    return ticket


def spend(purpose: str, ticket: str) -> int | None:
    """The user id a ticket names, deleting it in the same round trip; ``None``
    when there is no such ticket (unknown, expired, already spent)."""
    value = cache.getdel(_key(purpose, ticket))
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        logger.warning(
            "Ticket for %s holds a value that is not a user id", purpose
        )
        return None
    return value
