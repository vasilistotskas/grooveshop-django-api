"""Attempts at a platform-staff sign-in, counted before they are judged.

allauth's limiter covers ``/_allauth/`` only, and neither the Django admin
login nor the staff token endpoint goes through allauth, so a store Owner or
Admin (password only) could be guessed at the proxy's speed. Both entry
points reach the password check through
``PlatformStaffBackend.authenticate_staff``, which calls this module, so the
count is made in one place and the two share it.

**Count first.** ``register_attempt`` increments (``add`` with the window,
then ``incr``, using the value it returns) BEFORE the password is looked at,
and refuses once the result is over the limit, without checking the password
at all. Counting after the check would let every request in flight read a
count below the limit while Argon2 runs, so the budget would be "the limit
plus the parallel requests". A success clears the account counters.

Three counters, each with its own budget (``settings.STAFF_LOGIN_LIMITS``):

- **Account + proven address**: the strict one (default 5). A lock here
  refuses only that address, so someone who knows a staff email cannot lock
  the owner out from the owner's own address.
- **Account-wide ceiling** (default 50): bounds a guessing run spread over
  many addresses; it locks the account for everyone once exceeded, which is
  the remaining denial-of-service bound (see ``docs/api-staff-identity.md``).
  It is the only account limit for a request without a proven address.
- **Address**: a run of guesses across many accounts. A success does NOT
  clear it: one valid login of the attacker's own would otherwise reset the
  run.

An address is ``trusted_client_ip`` (the request carried our edge's
``X-Origin-Verify``), IPv6 reduced to its /64 so one allocation cannot rotate
freely. Without the proof (a domain outside our Cloudflare zones, a missing
or rotated secret, a direct call to a node) the only address known is the
proxy peer, a Cloudflare edge node or SNAT gateway shared by everyone behind
it, so counting it would let 20 wrong passwords from one place lock every
staff member out: no address counter applies and the ceiling alone bounds
the attacker.

The identifier is the submitted one, lower-cased and hashed, whether or not
it names an account, so a locked unknown address answers exactly like a
locked known one. A lock lasts until the window that began with the first
attempt ends; refused attempts do not extend it. The keys are ``global:``
(staff are public identities, so a count kept per schema would give every
store host its own allowance) and ``strict:`` (a cache that cannot count must
stop the sign-in, not lift the limit).
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
from contextlib import contextmanager

from django.conf import settings
from django.core.cache import cache
from django.http import HttpRequest

from core.caches import REDIS_UNAVAILABLE, STRICT_NAMESPACE
from core.client_ip import trusted_client_ip
from tenant.cache import GLOBAL_CACHE_PREFIX

logger = logging.getLogger(__name__)

_KEY = f"{GLOBAL_CACHE_PREFIX}{STRICT_NAMESPACE}staff_login.failures:"

#: IPv6 allocations are handed out by the /64.
_IPV6_PREFIX = 64


class StaffLoginLocked(Exception):
    """Too many attempts for this account, address or pair."""


class StaffLoginUnavailable(Exception):
    """The counters cannot be read or written (the cache is down). The
    sign-in is refused: an attacker who can overload Redis must not be
    able to lift the limits."""


@contextmanager
def refuse_when_cache_is_down():
    """Turn the strict keys' "cache unavailable" error into
    ``StaffLoginUnavailable``, logged once per attempt (no username)."""
    try:
        yield
    except REDIS_UNAVAILABLE as exc:
        logger.error(
            "Staff sign-in refused: the attempt counters are unavailable",
            exc_info=exc,
        )
        raise StaffLoginUnavailable from None


def _digest(value: str) -> str:
    return hashlib.sha256(value.strip().lower().encode()).hexdigest()


def _network(address: str) -> str:
    """The key an address counts under: the whole IPv4 address, the /64 of
    an IPv6 one."""
    try:
        parsed = ipaddress.ip_address(address.strip())
    except ValueError:
        return address.strip()
    if isinstance(parsed, ipaddress.IPv6Address):
        if parsed.ipv4_mapped is not None:
            return str(parsed.ipv4_mapped)
        return str(
            ipaddress.ip_network(f"{parsed}/{_IPV6_PREFIX}", strict=False)
        )
    return str(parsed)


def _proven_network(request: HttpRequest | None) -> str | None:
    if request is None:
        return None
    address = trusted_client_ip(request)
    return None if address is None else _network(address)


def count(key: str, window: int) -> int:
    """Add one to *key* (created with *window* seconds to live) and return
    the new value. ``add`` then ``incr`` is atomic in Redis, so parallel
    callers each get their own number."""
    cache.add(key, 0, timeout=window)
    try:
        return cache.incr(key)
    except ValueError:
        # Expired between the two calls.
        cache.set(key, 1, timeout=window)
        return 1


def _generation_key(username: str) -> str:
    return f"{_KEY}generation:{_digest(username)}"


def _generation(username: str) -> int:
    return cache.get(_generation_key(username), 0)


def _ceiling_key(username: str) -> str:
    return f"{_KEY}account:{_digest(username)}"


def _pair_key(username: str, network: str) -> str:
    return (
        f"{_KEY}pair:{_generation(username)}:{_digest(username)}:"
        f"{_digest(network)}"
    )


def _address_key(network: str) -> str:
    return f"{_KEY}address:{_digest(network)}"


def register_attempt(request: HttpRequest | None, username: str) -> None:
    """Count this attempt and raise ``StaffLoginLocked`` when it is over any
    budget, ``StaffLoginUnavailable`` when the cache cannot count. Call
    before the password is checked."""
    with refuse_when_cache_is_down():
        _register_attempt(request, username)


def _register_attempt(request: HttpRequest | None, username: str) -> None:
    limits = settings.STAFF_LOGIN_LIMITS
    window = limits["WINDOW_SECONDS"]
    network = _proven_network(request)
    over = (
        count(_ceiling_key(username), window)
        > limits["MAX_FAILURES_PER_ACCOUNT"]
    )
    if network is not None:
        over |= (
            count(_pair_key(username, network), window)
            > limits["MAX_FAILURES_PER_ACCOUNT_ADDRESS"]
        )
        over |= (
            count(_address_key(network), window) > limits["MAX_FAILURES_PER_IP"]
        )
    if over:
        logger.warning("Staff sign-in refused: too many attempts")
        raise StaffLoginLocked


def clear_after_success(request: HttpRequest | None, username: str) -> None:
    """A right password ends the account's counts for the address that
    proved it. The address counter stays."""
    with refuse_when_cache_is_down():
        cache.delete(_ceiling_key(username))
        network = _proven_network(request)
        if network is not None:
            cache.delete(_pair_key(username, network))


def clear_account(username: str) -> None:
    """Forget every count of the account, whatever the address: a password
    reset or an operator unlocking it. The address counters stay.

    The pair counters are named by a generation that moves here, so they
    need not be found."""
    with refuse_when_cache_is_down():
        cache.delete(_ceiling_key(username))
        cache.set(
            _generation_key(username),
            _generation(username) + 1,
            timeout=settings.STAFF_LOGIN_LIMITS["WINDOW_SECONDS"],
        )
