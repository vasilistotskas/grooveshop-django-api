"""Caches for the tests that need Redis itself: one whose Redis is down,
for what an outage does, and a private one on the suite's own Redis."""

from __future__ import annotations

import uuid
from os import getenv

from redis.backoff import NoBackoff
from redis.retry import Retry

from core.caches import CustomCache


def stopped_cache() -> CustomCache:
    """A real ``CustomCache`` against a port nothing listens on: every
    command fails at once with redis-py's ``ConnectionError``, as in an
    outage, with no retry and no health check to slow the test."""
    return CustomCache(
        server="redis://127.0.0.1:1/0",
        params={
            "OPTIONS": {
                "retry": Retry(NoBackoff(), 0),
                "socket_connect_timeout": 0.2,
                "health_check_interval": 0,
            }
        },
    )


def private_cache() -> CustomCache:
    """A real ``CustomCache`` on the suite's Redis, under a prefix of its
    own: what a test gives a throttle so no other test or xdist worker
    shares its history, with Redis's own atomic commands and locks (a
    ``LocMemCache`` has neither)."""
    host = getenv("REDIS_HOST", "localhost")
    port = getenv("REDIS_PORT", "6379")
    worker = getenv("PYTEST_XDIST_WORKER", "gw0")
    db = (int("".join(filter(str.isdigit, worker)) or "0") % 15) + 1
    return CustomCache(
        server=f"redis://{host}:{port}/{db}",
        params={"KEY_PREFIX": f"throttle-test-{uuid.uuid4().hex[:8]}"},
    )
