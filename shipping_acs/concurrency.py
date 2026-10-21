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

ACS publishes no concurrency or rate figure; the limit
(``ACS_MAX_CONCURRENT_REQUESTS``, default 2) comes from our own
measurement. Over 10 days of production logs (2026-10-11 to 10-20), each
tracking task was matched against the number of ACS tasks in flight when
it started: 0.9% rejected at 1, 2.0% at 2, 3.6% at 3, 4.0% at 4, 6.9% at
5, 7.9% at 6, 11% at 7, 17% at 8 and 19-50% at 9 or more. The full table
and the dispatch-spacing arithmetic are in ``docs/order-system.md``.

The semaphore is ``ACS_MAX_CONCURRENT_REQUESTS`` redis-py ``Lock`` keys
(``CustomCache.lock``), each non-blocking with a lease. A lock carries a
per-holder token and ``release`` deletes the key only while the token is
still its own, so a lease that ran out and was taken by another worker is
never deleted from under it. The lease is the worst case of one request
(``client.worst_case_request_seconds``), so a live request outlives it
only through the cases that function documents; a worker killed mid-call
frees its slot when the lease ends. Keys go through the tenant cache key
function, so each store's account has its own slots, and carry the ACS
``Company_ID`` so a store with more than one account would not share them.

A caller that finds every slot taken waits a bounded time, then raises
``AcsRetryableError`` so Celery retries with backoff instead of a worker
sitting on it. With Redis unreachable the cap is skipped and logged: it
only prevents 406s (a rejected call was never processed), and
``core.caches`` states that "an unreachable Redis must cost speed, not
requests", so a cache outage must not fail checkout quotes or voucher
mints.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Generator
from contextlib import contextmanager

from django.conf import settings
from django.core.cache import cache
from redis.exceptions import LockError
from redis.lock import Lock

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


def _acquire(account: str, alias: str, lease_seconds: float) -> Lock:
    limit = settings.ACS_MAX_CONCURRENT_REQUESTS
    wait = settings.ACS_SLOT_WAIT_SECONDS
    deadline = time.monotonic() + wait
    while True:
        # A random start spreads the contention across the slot keys.
        for slot in random.sample(range(limit), limit):
            lock = cache.lock(_slot_key(account, slot), timeout=lease_seconds)
            if lock.acquire():
                return lock
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
def acs_request_slot(
    *, account: str, alias: str, lease_seconds: float
) -> Generator[None]:
    """Hold one of the account's request slots for the ``with`` body.

    Raises :class:`AcsRetryableError` when no slot frees within
    ``ACS_SLOT_WAIT_SECONDS``. The slot is released on any exit,
    exceptions included, and only while this holder still owns it.
    ``lease_seconds`` is how long the slot survives a holder that dies.
    """
    try:
        lock = _acquire(account, alias, lease_seconds)
    except REDIS_UNAVAILABLE as exc:
        _redis_down.report(exc)
        yield
        return
    try:
        yield
    finally:
        try:
            lock.release()
        except LockError as exc:
            # The lease ran out and the slot is another holder's now (or
            # gone); leaving it alone is the point of the token.
            logger.warning(
                "ACS %s: request slot lease expired before release: %s",
                alias,
                exc,
            )
        except REDIS_UNAVAILABLE as exc:
            # The lease frees the slot; the ACS answer must not be lost.
            _redis_down.report(exc)
