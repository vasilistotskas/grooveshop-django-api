"""Cluster-wide cap on in-flight ACS requests, per ACS account.

ACS rejects overlapping calls from one account with HTTP 406 at the auth
layer. Production, 2026-10-20: 22% of tracking calls were rejected, every
one of them inside the ``poll_acs_tracking_batch`` fan-out, while the
same calls made alone succeed. Staggering the *start* of the poll tasks
does not bound the overlap, because each task makes two calls and ACS
answers in 0.6 to 4 s, so several pods end up with six to eight calls in
flight at once. The cap therefore lives where every call passes through,
``AcsClient._call``, and counts requests in flight rather than requests
started.

ACS publishes no concurrency or rate figure we could find (the manual is
not in this repository), so the limit is a setting
(``ACS_MAX_CONCURRENT_REQUESTS``) to be tuned against the 406 rate, not a
documented number.

The semaphore is ``ACS_MAX_CONCURRENT_REQUESTS`` slot keys, each taken
with ``cache.add`` (an atomic ``SET NX`` with a TTL) and deleted when the
request ends. The TTL is the safety net for a worker that dies holding a
slot: the slot frees itself. It is sized far above a request's timeout, so
a slot expiring under a live request is not a case the code defends
against. Keys go through the tenant cache key function, so each store's
account has its own slots, and carry the ACS ``Company_ID`` so a store
with more than one account would not share them.

A caller that finds every slot taken waits a bounded time, then raises
``AcsRetryableError`` so Celery retries with backoff instead of a worker
sitting on it. With Redis unreachable the limit is skipped (and logged):
a cache outage must cost the cap, not every ACS call, which is how the
rest of the cache layer treats it (``core.caches``).
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Generator
from contextlib import contextmanager

from django.conf import settings
from django.core.cache import cache

from core.caches import REDIS_UNAVAILABLE, BurstLogger
from shipping_acs.exceptions import AcsRetryableError

logger = logging.getLogger("shipping_acs.concurrency")

_redis_down = BurstLogger(
    "ACS request cap skipped, Redis unavailable: %s "
    "(%s similar failure(s) suppressed in the last %ss)",
    target=logger,
)

# Pause between passes over the slots while waiting for one to free.
# Jittered so the waiting workers do not poll in step.
_POLL_INTERVAL_RANGE = (0.05, 0.15)


def _slot_key(account: str, slot: int) -> str:
    return f"acs:slot:{account}:{slot}"


def _acquire(account: str, alias: str) -> str:
    limit = settings.ACS_MAX_CONCURRENT_REQUESTS
    ttl = settings.ACS_SLOT_TTL_SECONDS
    wait = settings.ACS_SLOT_WAIT_SECONDS
    deadline = time.monotonic() + wait
    while True:
        # A random start spreads the contention across the slot keys.
        for slot in random.sample(range(limit), limit):
            key = _slot_key(account, slot)
            if cache.add(key, 1, ttl):
                return key
        if time.monotonic() >= deadline:
            logger.warning(
                "ACS %s: all %s request slots busy for %ss, retrying later",
                alias,
                limit,
                wait,
            )
            raise AcsRetryableError(
                alias=alias,
                error_message=(
                    f"ACS request cap: no slot of {limit} free within {wait}s"
                ),
            )
        time.sleep(random.uniform(*_POLL_INTERVAL_RANGE))


@contextmanager
def acs_request_slot(*, account: str, alias: str) -> Generator[None]:
    """Hold one of the account's request slots for the ``with`` body.

    Raises :class:`AcsRetryableError` when no slot frees within
    ``ACS_SLOT_WAIT_SECONDS``. The slot is released on any exit,
    exceptions included.
    """
    try:
        key = _acquire(account, alias)
    except REDIS_UNAVAILABLE as exc:
        _redis_down.report(exc)
        yield
        return
    try:
        yield
    finally:
        cache.delete(key)
