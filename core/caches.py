from __future__ import annotations

import logging
from typing import Any, Awaitable, cast

from django.conf import settings
from django.core.cache.backends.redis import RedisCache, RedisCacheClient

logger = logging.getLogger(__name__)

ONE_HOUR = 60 * 60
ONE_DAY = ONE_HOUR * 24
ONE_WEEK = ONE_DAY * 7
ONE_MONTH = ONE_DAY * 30
ONE_YEAR = ONE_DAY * 365

SESSION_PREFIX = "session:"

_SCAN_BATCH_SIZE = 500


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


# The configured default backend (a CustomCache via CACHES["default"])
# — NOT a second, directly-constructed client. A standalone instance
# had no KEY_PREFIX/KEY_FUNCTION, so its get/set landed outside the
# tenant namespace and its SCAN helpers walked every tenant's keys.
