"""``cache.clear()`` inside a test must not reach other xdist workers.

Every worker shares one Redis database. The suite backend,
``tests.cache.WorkerScopedCache``, scopes ``clear()`` to this worker's
key namespace; a real ``FLUSHDB`` here evicted the other workers' live
keys and made their warm-cache query budgets fail at random.
"""

from __future__ import annotations

import uuid

from django.core.cache import caches

from tests.cache import WorkerScopedCache


def test_clear_spares_other_workers_keys_and_drops_this_workers():
    cache = caches["default"]
    assert isinstance(cache, WorkerScopedCache)
    client = cache._cache.get_client(None, write=True)
    foreign = f"public:test_other_worker_{uuid.uuid4().hex}:1:recs:ctx"
    own = f"clear-probe-{uuid.uuid4().hex}"

    client.set(foreign, b"1")
    cache.set(own, "1")
    try:
        cache.clear()

        assert cache.get(own) is None
        assert client.exists(foreign) == 1
    finally:
        client.delete(foreign)
