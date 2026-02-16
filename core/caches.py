from __future__ import annotations

import logging
from os import getenv
from typing import Any, Awaitable

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
      instead of FLUSHDB, safe for shared Redis instances.
    - ``keys()`` / ``delete_raw_keys()`` -- admin cache-management
      utilities for pattern-based key inspection and deletion.
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
            pattern = f"{prefix}*"
            deleted = 0
            batch: list[str | bytes] = []

            for key in client.scan_iter(match=pattern, count=_SCAN_BATCH_SIZE):
                batch.append(key)
                if len(batch) >= _SCAN_BATCH_SIZE:
                    deleted += client.unlink(*batch)
                    batch.clear()

            if batch:
                deleted += client.unlink(*batch)

            results[prefix] = deleted
            logger.info("Cleared %d keys with prefix '%s'", deleted, prefix)

        return results

    @staticmethod
    def _make_pattern(search: str | None = None) -> str:
        if search is None:
            return "*"
        return f"*{search}*"


REDIS_HOST = getenv("REDIS_HOST", "localhost")
REDIS_PORT = getenv("REDIS_PORT", "6379")
REDIS_URL = f"redis://{REDIS_HOST}:{REDIS_PORT}/0"
cache_instance = CustomCache(server=REDIS_URL, params={})
