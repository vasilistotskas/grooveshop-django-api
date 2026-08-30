"""``CACHES["default"]["KEY_FUNCTION"] = tenant.cache.make_tenant_key``
prefixes every cache key with the ACTIVE schema
(``{schema}:{key_prefix}:{version}:{key}``), so the same raw key set
from two different tenants must never collide. The main suite runs
with ``DISABLE_CACHE = True`` and a LocMem backend, so this real-Redis,
real-schema behaviour is never exercised there.

Uses a real Redis connection (this lane's CI job runs the same Redis
service as the main test job) — cleans up its own keys since a cache
write is not rolled back by the DB transaction the way ORM writes are.
"""

from __future__ import annotations

import pytest
from django.core.cache import cache
from django_tenants.utils import schema_context

_KEY = "tests_mt_cache_isolation_probe"


@pytest.fixture(autouse=True)
def _cleanup_probe_key():
    yield
    with schema_context("public"):
        cache.delete(_KEY)


@pytest.mark.django_db
def test_cache_key_is_schema_scoped(mt_tenant):
    with schema_context(mt_tenant.schema_name):
        cache.delete(_KEY)
        cache.set(_KEY, "tenant-value")
        assert cache.get(_KEY) == "tenant-value"

    with schema_context("public"):
        assert cache.get(_KEY) is None, (
            "a cache key set inside the tenant schema was readable from "
            "public — KEY_FUNCTION is not schema-scoping cache keys"
        )
        cache.set(_KEY, "public-value")

    with schema_context(mt_tenant.schema_name):
        assert cache.get(_KEY) == "tenant-value", (
            "public's write under the same raw key clobbered the "
            "tenant's cached value — cache keys are colliding cross-schema"
        )
