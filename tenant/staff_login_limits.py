"""Wrong passwords at a platform-staff sign-in, counted per account and per
client address.

allauth's limiter covers ``/_allauth/`` only, and neither the Django admin
login nor the staff token endpoint goes through allauth, so a store Owner or
Admin (password only) could be guessed at the proxy's speed. Both entry
points reach the password check through
``PlatformStaffBackend.authenticate_staff``, which calls this module, so the
count is made in one place and the two share it.

- **Per account**: the submitted identifier, lower-cased and hashed, whether
  or not it names an account, so a locked unknown address answers exactly
  like a locked known one and the lock reveals nothing about who exists.
  A success clears it, and so does a password reset.
- **Per address** (``client_ip_or_peer``): a run of guesses across many
  accounts. A success does NOT clear it: one valid login of the attacker's
  own would otherwise reset the run.

A lock refuses even the correct password and lasts until the window that
began with the first failure ends; refused attempts add nothing, so they
cannot extend it. The keys are ``global:`` (staff are public identities, so
a count kept per schema would give every store host its own allowance) and
``strict:`` (a cache that cannot count must stop the sign-in, not lift the
limit). Budgets: ``settings.STAFF_LOGIN_LIMITS``.
"""

from __future__ import annotations

import hashlib
import logging

from django.conf import settings
from django.core.cache import cache
from django.http import HttpRequest

from core.caches import STRICT_NAMESPACE
from core.client_ip import client_ip_or_peer
from tenant.cache import GLOBAL_CACHE_PREFIX

logger = logging.getLogger(__name__)

_KEY = f"{GLOBAL_CACHE_PREFIX}{STRICT_NAMESPACE}staff_login.failures:"


class StaffLoginLocked(Exception):
    """Too many wrong passwords for this account or from this address."""


def _digest(value: str) -> str:
    return hashlib.sha256(value.strip().lower().encode()).hexdigest()


def _account_key(username: str) -> str:
    return f"{_KEY}account:{_digest(username)}"


def _address_key(request: HttpRequest | None) -> str | None:
    if request is None:
        return None
    return f"{_KEY}address:{_digest(client_ip_or_peer(request))}"


def ensure_not_locked(request: HttpRequest | None, username: str) -> None:
    """Raise ``StaffLoginLocked`` when either count is spent."""
    limits = settings.STAFF_LOGIN_LIMITS
    address_key = _address_key(request)
    if (
        cache.get(_account_key(username), 0)
        >= limits["MAX_FAILURES_PER_ACCOUNT"]
        or address_key is not None
        and cache.get(address_key, 0) >= limits["MAX_FAILURES_PER_IP"]
    ):
        raise StaffLoginLocked


def _count(key: str) -> None:
    window = settings.STAFF_LOGIN_LIMITS["WINDOW_SECONDS"]
    cache.add(key, 0, timeout=window)
    try:
        cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=window)


def record_failure(request: HttpRequest | None, username: str) -> None:
    _count(_account_key(username))
    address_key = _address_key(request)
    if address_key is not None:
        _count(address_key)
    logger.warning("Wrong staff password")


def clear_account(username: str) -> None:
    """Forget the account's wrong passwords: after a success, a password
    reset or an operator unlocking it. The address count stays."""
    cache.delete(_account_key(username))
