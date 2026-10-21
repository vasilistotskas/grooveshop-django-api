from __future__ import annotations

import logging
from dataclasses import dataclass

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


@dataclass
class NuxtPurgeResult:
    matched: int
    deleted: int
    blocked: int
    error: str | None = None


def _resolve_endpoint() -> str | None:
    """Return the absolute Nuxt purge URL or ``None`` if not configured."""

    base = getattr(settings, "NUXT_INTERNAL_BASE_URL", "") or ""
    if not base:
        return None
    return f"{base.rstrip('/')}/api/admin/cache/purge"


def _resolve_token() -> str | None:
    return getattr(settings, "NUXT_CACHE_PURGE_TOKEN", "") or None


class StoreHostUnresolvedError(Exception):
    """A purge runs in a store's schema but the store's host is unknown."""

    def __init__(self, schema_name: str) -> None:
        self.schema_name = schema_name
        super().__init__(f"no primary domain for store schema {schema_name!r}")


def _current_tenant_host() -> str | None:
    """The purging store's primary storefront domain; None for a platform purge.

    A merchant cache purge runs inside that tenant's schema context, so
    ``connection.tenant`` is the store issuing it. Passing its host to
    the Nuxt purge endpoint scopes the eviction to that store's cached
    keys (the SSR cache is shared across every tenant). None only on the
    public schema (or with no tenant bound at all) — a platform purge is
    deliberately global.

    A store whose host cannot be found must NOT come back as None, or its
    purge would go out host-less and evict every store's pages. Nothing
    in the model guarantees a primary domain (``DomainMixin.save`` only
    promotes a store's first domain; a store with none, or whose domains
    an admin removed, has none), so this raises
    :class:`StoreHostUnresolvedError` instead.
    """
    from django.db import connection
    from django_tenants.utils import get_public_schema_name

    from tenant.membership import bound_tenant

    current = getattr(connection, "tenant", None)
    schema_name = getattr(current, "schema_name", None)
    if current is None or schema_name == get_public_schema_name():
        return None

    tenant = bound_tenant()
    domains = getattr(tenant, "domains", None)
    primary = (
        domains.filter(is_primary=True).first() if domains is not None else None
    )
    if primary is None:
        raise StoreHostUnresolvedError(str(schema_name))
    return primary.domain


def is_configured() -> bool:
    return bool(_resolve_endpoint() and _resolve_token())


def request_purge(
    patterns: list[str],
    *,
    dry_run: bool = False,
    tenant_config: bool = False,
    timeout: float = 5.0,
) -> NuxtPurgeResult:
    """Call the Nuxt purge endpoint with the supplied glob patterns.

    Returns a structured result; never raises so admin actions stay
    responsive when the frontend is briefly unreachable.

    ``tenant_config`` tells the storefront this purge follows a change to
    the store's tenant config, so it re-resolves the store and re-keys
    its rendered pages; any other purge leaves both alone.
    """

    if not patterns:
        return NuxtPurgeResult(matched=0, deleted=0, blocked=0)

    endpoint = _resolve_endpoint()
    token = _resolve_token()
    if not endpoint or not token:
        msg = "Nuxt purge endpoint is not configured"
        logger.warning(msg)
        return NuxtPurgeResult(matched=0, deleted=0, blocked=0, error=msg)

    payload: dict[str, list[str] | bool | str] = {
        "patterns": patterns,
        "dryRun": dry_run,
    }
    if tenant_config:
        payload["tenantConfig"] = True
    try:
        host = _current_tenant_host()
    except StoreHostUnresolvedError as exc:
        # Sending this without a host would be a platform-wide purge.
        logger.error(
            "Nuxt cache purge skipped for store schema %s: %s; the store's "
            "cached pages were not purged",
            exc.schema_name,
            exc,
        )
        return NuxtPurgeResult(
            matched=0,
            deleted=0,
            blocked=0,
            error=f"store host unresolved for {exc.schema_name}",
        )
    if host:
        payload["host"] = host
    headers = {
        "X-Cache-Purge-Token": token,
        "Content-Type": "application/json",
    }
    try:
        response = requests.post(
            endpoint, json=payload, headers=headers, timeout=timeout
        )
        response.raise_for_status()
        data = response.json()
        return NuxtPurgeResult(
            matched=int(data.get("matched", 0)),
            deleted=int(data.get("deleted", 0)),
            blocked=int(data.get("blocked", 0)),
        )
    except requests.RequestException as exc:
        logger.warning("Nuxt cache purge failed: %s", exc)
        return NuxtPurgeResult(matched=0, deleted=0, blocked=0, error=str(exc))
