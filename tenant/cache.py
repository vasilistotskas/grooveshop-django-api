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

import hashlib
from functools import lru_cache

from django.db import connection

GLOBAL_CACHE_PREFIX = "global:"


def make_tenant_key(key: str, key_prefix: str, version: int) -> str:
    if key.startswith(GLOBAL_CACHE_PREFIX):
        schema = "global"
    else:
        schema = getattr(connection, "schema_name", "public")
    return f"{schema}:{key_prefix}:{version}:{key}"


@lru_cache(maxsize=1)
def _tenant_config_shape() -> str:
    """Short fingerprint of ``TenantConfigSerializer``'s field names."""
    from tenant.serializers import TenantConfigSerializer

    names = ",".join(sorted(TenantConfigSerializer().get_fields()))
    return hashlib.blake2s(names.encode(), digest_size=4).hexdigest()


def tenant_resolve_key(domain: str) -> str:
    """Cache key for a domain's resolved ``TenantConfig`` payload.

    The SHAPE fingerprint is load-bearing, not decoration. What is
    cached is the SERIALIZED payload, so a release that adds a field to
    ``TenantConfigSerializer`` keeps serving the old shape until the TTL
    expires — and the storefront validates this response against a
    generated Zod schema in which the new field is REQUIRED. The
    frontend then rejects every resolve and the tenant middleware turns
    that into a 404 "Store not found" on every route, for every tenant.

    That is not hypothetical: adding ``b2bEnabled`` took production down
    on 2026-08-31 until the stale key was deleted by hand. Deriving the
    key from the field names means a changed payload shape simply reads
    a different key — the old entry is never consulted again and ages
    out on its own TTL, with no deploy step to remember.

    Every reader AND invalidator must go through this helper, or a
    signal's delete silently stops matching the writer's key.
    """
    return (
        f"{GLOBAL_CACHE_PREFIX}tenant_resolve:{_tenant_config_shape()}:{domain}"
    )
