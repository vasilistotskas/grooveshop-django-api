"""The cache backend the test suite runs on (``tests/settings.py``)."""

from __future__ import annotations

from core.caches import CustomCache


class WorkerScopedCache(CustomCache):
    """The production backend, with ``clear()`` limited to one worker.

    Every pytest-xdist worker shares one Redis database, and each is
    given its own ``KEY_PREFIX`` in ``tests/settings.py``. On
    ``RedisCache`` a ``clear()`` is ``FLUSHDB``, so a test calling
    ``cache.clear()`` (to evict parler's translation cache after a
    queryset ``.update()``, to reset throttle counters, ...) also wiped
    whatever the OTHER workers had cached at that instant. Any test
    whose query count depends on a warm cache then failed at a rate set
    by whatever ran beside it — observed in CI as
    ``test_cost_does_not_grow_with_the_number_of_seeds`` "grew from 11
    to 17 queries", and reproduced locally with a second process
    issuing ``FLUSHDB`` at random: 53 of 80 runs failed.

    Declared as the backend, not patched onto an instance, so every
    instance Django builds has it — including the one rebuilt after an
    ``override_settings(CACHES=...)`` exits
    (``django.test.signals.clear_cache_handlers``). A ``CustomCache`` a
    test builds for itself keeps the real ``FLUSHDB``.
    """

    def clear(self) -> None:
        client = self._cache.get_client(None, write=True)
        # ``tenant.cache.make_tenant_key`` puts the ACTIVE SCHEMA in
        # front of every key (``{schema}:{prefix}:{version}:{key}``), so
        # the worker namespace sits in the second segment. The bare
        # pattern covers a key built without that function.
        patterns = (f"{self.key_prefix}:*", f"*:{self.key_prefix}:*")
        keys = [
            key
            for pattern in patterns
            for key in client.scan_iter(match=pattern, count=1000)
        ]
        if keys:
            client.delete(*keys)
