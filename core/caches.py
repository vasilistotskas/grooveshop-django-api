from __future__ import annotations

import logging
from collections.abc import Awaitable
from typing import Any, cast

from django.conf import settings
from django.core.cache.backends.redis import RedisCache, RedisCacheClient
from redis.backoff import ExponentialBackoff
from redis.retry import Retry

logger = logging.getLogger(__name__)


_SCAN_BATCH_SIZE = 500

# redis-py ships every resilience feature OFF by default: the sync
# Connection defaults are ``retry=None``, ``health_check_interval=0``
# and ``socket_keepalive=False``. Django never overrides them, so a
# pooled connection that died while idle — Redis ``timeout``, a pod
# rollover, a NAT/conntrack eviction — is handed straight back out and
# the next command raises ConnectionResetError at the call site.
#
# That is not a test-only concern: with no OPTIONS on ``CACHES`` the
# production cache had exactly the same exposure, so a single reset
# surfaced as a 500 rather than a retried command.
#
# - ``health_check_interval`` PINGs a connection idle longer than N
#   seconds before reuse and transparently reconnects a dead one.
# - ``socket_keepalive`` lets the OS notice a peer that vanished
#   without a FIN.
# - ``retry`` re-runs the command on ConnectionError/TimeoutError
#   (redis-py's default ``supported_errors``); the backoff is
#   sub-second in total, so a request never visibly stalls.
#
# The Retry template is shared, which is safe: redis-py deep-copies it
# per connection (``AbstractConnection.__init__``).
_RESILIENCE_OPTIONS: dict[str, Any] = {
    "health_check_interval": 30,
    "socket_keepalive": True,
    "retry": Retry(ExponentialBackoff(), retries=3),
}


class CustomCache(RedisCache):
    """
    Redis cache backend with prefix-aware clearing and key inspection.

    Provides:
    - ``clear_by_prefixes()`` -- selectively clear keys by prefix
      instead of FLUSHDB, safe for shared Redis instances. Platform-
      wide (all schemas) by design — used by the public-schema
      ``clear_all_cache`` beat task and the clear_cache command.
    - ``keys()`` / ``delete_raw_keys()`` -- admin cache-management
      utilities for pattern-based key inspection and deletion,
      scoped to the ACTIVE schema (raw layout per
      ``tenant.cache.make_tenant_key``: ``{schema}:{prefix}:{version}:
      {key}``) so one tenant's admin purge can never UNLINK another
      tenant's keys.

    Registered as ``CACHES["default"]`` so these helpers share the
    tenant-keyed backend every ``cache.get/set`` uses.
    """

    _cache: RedisCacheClient

    def __init__(self, server: Any, params: dict[str, Any]) -> None:
        """Apply the connection-resilience defaults.

        Set here rather than in ``CACHES["default"]["OPTIONS"]`` because
        this backend is also instantiated directly (tests, and any
        caller building a cache for a specific Redis DB), and those
        paths pass ``params={}`` — a settings-only fix would leave them
        on redis-py's bare defaults.

        Anything explicitly configured in ``OPTIONS`` still wins.
        ``params`` is copied, never mutated: Django hands us the live
        ``settings.CACHES`` entry.
        """
        params = {
            **params,
            "OPTIONS": {
                **_RESILIENCE_OPTIONS,
                **(params.get("OPTIONS") or {}),
            },
        }
        super().__init__(server, params)

    def keys(self, search: str | None = None) -> list[str]:
        """
        Return raw Redis keys matching *search* via SCAN.

        The returned strings are the literal keys stored in Redis
        (e.g. ``redis:1:views.decorators.cache…``).  Use
        :meth:`delete_raw_keys` to remove them — **not** the regular
        ``delete()`` method which would re-apply ``make_key()``.
        """
        try:
            pattern = self._make_pattern(search)
            raw_keys: list[str] = []
            for key in self._cache.get_client().scan_iter(
                match=pattern, count=_SCAN_BATCH_SIZE
            ):
                raw_keys.append(
                    key.decode("utf-8") if isinstance(key, bytes) else key
                )
            raw_keys.sort()
            return raw_keys
        except Exception as exc:
            logger.warning("Error getting cache keys: %s", str(exc))
            return []

    def delete_raw_keys(
        self, raw_keys: list[str]
    ) -> int | Awaitable[Any] | Any:
        """
        Delete keys directly in Redis without ``make_key`` transformation.

        Uses UNLINK (non-blocking) for better performance.
        Returns the number of keys actually deleted.
        """
        if not raw_keys:
            return 0
        try:
            client = self._cache.get_client()
            return client.unlink(*raw_keys)
        except Exception as exc:
            logger.warning("Error deleting raw cache keys: %s", str(exc))
            return 0

    def clear_by_prefixes(
        self, prefixes: list[str] | None = None
    ) -> dict[str, int]:
        """
        Selectively clear Redis keys matching the given prefixes.

        Unlike ``clear()`` (which calls FLUSHDB), this method only
        removes keys whose raw name starts with one of the specified
        prefixes.  Safe for shared Redis instances where other services
        store keys in the same database.

        Args:
            prefixes: Key prefixes to clear. Defaults to
                ``settings.CACHE_CLEAR_PREFIXES``.

        Returns:
            Dict mapping each prefix to the number of keys deleted.
        """
        if prefixes is None:
            prefixes = getattr(settings, "CACHE_CLEAR_PREFIXES", [])

        if not prefixes:
            logger.warning(
                "No prefixes configured for cache clearing "
                "(CACHE_CLEAR_PREFIXES is empty)"
            )
            return {}

        client = self._cache.get_client()
        results: dict[str, int] = {}

        for prefix in prefixes:
            # Two raw layouts share the Redis DB: Django keys carry the
            # tenant schema first ({schema}:{prefix}{key} via
            # make_tenant_key), while non-Django keys (e.g. Nuxt's
            # ``cache:``) start with the prefix directly. Scan both so
            # the platform-wide clear covers every schema.
            deleted = 0
            batch: list[str | bytes] = []
            for pattern in (f"{prefix}*", f"*:{prefix}*"):
                for key in client.scan_iter(
                    match=pattern, count=_SCAN_BATCH_SIZE
                ):
                    batch.append(key)
                    if len(batch) >= _SCAN_BATCH_SIZE:
                        deleted += cast(int, client.unlink(*batch))
                        batch.clear()

                if batch:
                    deleted += cast(int, client.unlink(*batch))
                    batch.clear()

            results[prefix] = deleted
            logger.info("Cleared %d keys with prefix '%s'", deleted, prefix)

        return results

    def _make_pattern(self, search: str | None = None) -> str:
        """SCAN pattern scoped to this backend's key namespace.

        Built through ``make_key`` so it follows the configured
        KEY_FUNCTION — with ``tenant.cache.make_tenant_key`` the
        pattern embeds the ACTIVE schema
        (``{schema}:{prefix}:{version}:…``), so admin purge and key
        inspection from a tenant admin only ever see that tenant's
        keys (and the platform admin only public's).
        """
        if search is None:
            return self.make_key("*")
        return self.make_key(f"*{search}*")
