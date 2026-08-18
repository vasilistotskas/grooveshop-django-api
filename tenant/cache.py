"""Cache key namespacing for tenant-scoped and platform-global entries.

Registered as ``CACHES["default"]["KEY_FUNCTION"]``. Every
``cache.get``/``set``/``delete`` call in the process routes through
``make_tenant_key``, which prefixes the ACTIVE SCHEMA onto the raw key
so tenant A's cached data is never visible to (or clobbered by) tenant
B: ``{schema}:{key_prefix}:{version}:{key}``.

Some cache families are intentionally schema-INDEPENDENT — e.g. the
``tenant_resolve`` / ``tenant_domains`` lookups, whose writer can run
in ANY schema (a request can arrive on any tenant's domain and ask to
resolve a *different* domain) while the invalidating signal always
fires from the public schema (admin/API contexts that mutate
``TenantDomain``/``Tenant`` are gated to public). A schema-prefixed
key for these families means the invalidation almost never matches
the write, so a changed/deleted domain keeps resolving from a stale
cache entry until it expires.

Callers that need schema-independence opt in by prefixing their raw
key with the literal ``"global:"`` segment (see
``GLOBAL_CACHE_PREFIX``). ``make_tenant_key`` then substitutes the
fixed literal segment ``"global"`` in place of
``connection.schema_name``, so the exact same Redis key is produced
(and can be read/invalidated) from every schema:
``global:{key_prefix}:{version}:{key}`` — where ``key`` itself still
carries the ``global:`` prefix the caller chose, e.g.
``global:tenant_resolve:example.com``.
"""

from __future__ import annotations

from django.db import connection

GLOBAL_CACHE_PREFIX = "global:"


def make_tenant_key(key: str, key_prefix: str, version: int) -> str:
    if key.startswith(GLOBAL_CACHE_PREFIX):
        schema = "global"
    else:
        schema = getattr(connection, "schema_name", "public")
    return f"{schema}:{key_prefix}:{version}:{key}"
