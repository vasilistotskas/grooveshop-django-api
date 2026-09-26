"""Cache key namespacing for tenant-scoped and platform-global entries.

Registered as ``CACHES["default"]["KEY_FUNCTION"]``. Every
``cache.get``/``set``/``delete`` call in the process routes through
``make_tenant_key``, which prefixes the ACTIVE SCHEMA onto the raw key
so tenant A's cached data is never visible to (or clobbered by) tenant
B: ``{schema}:{key_prefix}:{version}:{key}``.

Some cache families are intentionally schema-INDEPENDENT — the
``tenant_resolve`` / ``tenant_domains`` lookups, whose reader can run
in ANY schema (a request can arrive on any tenant's domain and ask to
resolve a *different* domain). One entry per tenant, not one per
schema that happened to ask.

Callers that need schema-independence opt in by prefixing their raw
key with the literal ``"global:"`` segment (see
``GLOBAL_CACHE_PREFIX``). ``make_tenant_key`` then substitutes the
fixed literal segment ``"global"`` in place of
``connection.schema_name``, so the exact same Redis key is produced
from every schema: ``global:{key_prefix}:{version}:{key}`` — where
``key`` itself still carries the ``global:`` prefix the caller chose.

Invalidation is by GENERATION, never by delete
----------------------------------------------

Both families carry ``Tenant.cache_generation`` in the key, and every
write that changes what they hold bumps it in the database, in the same
statement or transaction as the write (``Tenant.save``,
``bump_cache_generation``). After the commit every reader builds a new
key and the old entry is never read again; it ages out on its TTL.

A delete-based purge cannot give that guarantee here. ``CustomCache``
fails open (``core/caches.py``): with Redis unreachable a ``delete`` is
a logged no-op, so a purge that ran during a blip left the pre-write
payload in Redis for the rest of its TTL — an hour for the resolve
payload, served again the moment Redis came back. Making these keys
strict instead would raise on READS too (``_is_strict`` guards ``get``
as well as ``delete``), and the resolve endpoint is on every storefront
request. A generation in the database keeps reads fail-open and makes
invalidation independent of Redis altogether. It also closes the race
a delete never could: a reader that loaded the pre-commit row and
cached it after the purge wrote it under the OLD generation, where
nobody looks any more.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from django.db import connection
from django.db.models import F

if TYPE_CHECKING:
    from tenant.models import Tenant

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


def _generation(tenant: Tenant) -> str:
    """``{pk}.{generation}`` — the pk as well, because a domain can move
    to another tenant whose counter happens to hold the same number."""
    return f"{tenant.pk}.{tenant.cache_generation}"


def tenant_resolve_key(domain: str, tenant: Tenant) -> str:
    """Cache key for a domain's resolved ``TenantConfig`` payload.

    *tenant* is the row the domain resolves to, read in the same query
    that found the domain (``tenant.views.tenant_resolve``), so the key
    names the generation that row was at.

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
    """
    return (
        f"{GLOBAL_CACHE_PREFIX}tenant_resolve:{_tenant_config_shape()}:"
        f"{_generation(tenant)}:{domain}"
    )


def tenant_domains_key(tenant: Tenant) -> str:
    """Cache key for a tenant's registered hostnames
    (``tenant.middleware.tenant_domain_set``)."""
    return (
        f"{GLOBAL_CACHE_PREFIX}tenant_domains:{tenant.schema_name}:"
        f"{_generation(tenant)}"
    )


def bump_cache_generation(**lookup: Any) -> None:
    """Move the matching tenant's cached entries to a new generation.

    For writes to rows OTHER than the ``Tenant`` itself that the cached
    entries are built from (``Tenant.save`` bumps its own). Call it from
    inside the write's transaction — a ``post_save`` receiver runs there
    — so the new generation becomes visible exactly when the write does.
    A relative UPDATE, so it can never move a counter backwards.
    """
    from tenant.models import Tenant

    Tenant.objects.filter(**lookup).update(
        cache_generation=F("cache_generation") + 1
    )
