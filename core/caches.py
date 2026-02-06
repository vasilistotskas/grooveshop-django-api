from __future__ import annotations

import logging
import os
from os import getenv

import redis
from django.core.cache.backends.redis import RedisCache, RedisCacheClient

logger = logging.getLogger(__name__)

ONE_HOUR = 60 * 60
ONE_DAY = ONE_HOUR * 24
ONE_WEEK = ONE_DAY * 7
ONE_MONTH = ONE_DAY * 30
ONE_YEAR = ONE_DAY * 365

SESSION_PREFIX = "session:"

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings")


class CustomCache(RedisCache):
    """
    Custom Redis cache backend with key inspection and raw deletion.

    Provides utility methods for the admin cache-management UI to scan
    for keys by pattern and delete them by their raw Redis key (bypassing
    Django's ``make_key`` transformation).
    """

    _cache: RedisCacheClient

    def __init__(self, server: str, params: dict) -> None:
        super().__init__(server, params)
        try:
            self._cache.get_client().ping()
            logger.info("Successfully connected to Redis cache")
        except (redis.exceptions.ConnectionError, Exception) as exc:
            logger.error("Error connecting to cache: %s", str(exc))

    def keys(self, search: str | None = None) -> list[str]:
        """
        Return raw Redis keys matching *search* via SCAN.

        The returned strings are the literal keys stored in Redis
        (e.g. ``default:1:views.decorators.cache…``).  Use
        :meth:`delete_raw_keys` to remove them — **not** the regular
        ``delete()`` method which would re-apply ``make_key()``.
        """
        try:
            pattern = self._make_pattern(search)
            raw_keys: list[str] = []
            for key in self._cache.get_client().scan_iter(match=pattern):
                raw_keys.append(key.decode("utf-8"))
            raw_keys.sort()
            return raw_keys
        except Exception as exc:
            logger.warning("Error getting cache keys: %s", str(exc))
            return []

    def delete_raw_keys(self, raw_keys: list[str]) -> int:
        """
        Delete keys directly in Redis without ``make_key`` transformation.

        Returns the number of keys actually deleted.
        """
        if not raw_keys:
            return 0
        try:
            client = self._cache.get_client()
            return client.delete(*raw_keys)
        except Exception as exc:
            logger.warning("Error deleting raw cache keys: %s", str(exc))
            return 0

    @staticmethod
    def _make_pattern(search: str | None = None) -> str:
        if search is None:
            return "*"
        return f"*{search}*"


REDIS_HOST = getenv("REDIS_HOST", "localhost")
REDIS_PORT = getenv("REDIS_PORT", "6379")
REDIS_URL = f"redis://{REDIS_HOST}:{REDIS_PORT}/0"
cache_instance = CustomCache(server=REDIS_URL, params={})
