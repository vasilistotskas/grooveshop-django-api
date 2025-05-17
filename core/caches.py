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
    _cache: RedisCacheClient

    def __init__(self, server, params):
        super().__init__(server, params)

        try:
            self._cache.get_client().ping()
        except (redis.exceptions.ConnectionError, Exception) as exc:
            logger.error("Error connecting to cache: %s", str(exc))

    def make_key(self, key, version=None):
        return key

    def keys(self, search: str | None = None):
        try:
            pattern = self._make_pattern(search)
            logger.info(f"Searching for keys with pattern: {pattern}")
            cache_keys = list(self._cache.get_client().scan_iter(match=pattern))
            keys_with_prefix = [key.decode("utf-8") for key in cache_keys]
            keys_with_prefix.sort()
            logger.info(f"Found keys: {keys_with_prefix}")
            return keys_with_prefix
        except Exception as exc:
            logger.warning("Error getting cache keys: %s", str(exc))
            return []

    @staticmethod
    def _make_pattern(search: str | None = None):
        if search is None:
            return "*"
        return f"*{search}*"


REDIS_HOST = getenv("REDIS_HOST", "localhost")
REDIS_PORT = getenv("REDIS_PORT", "6379")
REDIS_URL = f"redis://{REDIS_HOST}:{REDIS_PORT}/0"
cache_instance = CustomCache(server=REDIS_URL, params={})
