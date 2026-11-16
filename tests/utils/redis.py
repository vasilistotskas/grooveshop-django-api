"""A cache whose Redis is down, for the tests of what an outage does."""

from __future__ import annotations

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
