from __future__ import annotations

import os
from os import getenv
from typing import Any

import redis
from django.core.cache.backends.redis import RedisCache
from django.core.cache.backends.redis import RedisCacheClient

from core.logging import LogInfo

# Constants
DEFAULT_CACHE_ALIAS = "default"

ONE_HOUR = 60 * 60
ONE_DAY = ONE_HOUR * 24
ONE_WEEK = ONE_DAY * 7
ONE_MONTH = ONE_DAY * 30
ONE_YEAR = ONE_DAY * 365

# Cache Keys
SESSION_PREFIX = "session:"

# structure should be like "<USER_AUTHENTICATED>:<user_pk>:<random_session_key>"
USER_AUTHENTICATED = f"{SESSION_PREFIX}user_authenticated:"
USER_UNAUTHENTICATED = f"{SESSION_PREFIX}user_unauthenticated:"

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings")


class CustomCache(RedisCache):
    _cache: RedisCacheClient

    def __init__(self, server, params):
        super().__init__(server, params)

        try:
            self._cache.get_client().ping()
        except (redis.exceptions.ConnectionError, Exception) as exc:
            LogInfo.error("Error connecting to cache: %s", str(exc))

    def keys(self, search: str | None = None) -> list[str]:
        try:
            pattern = self._make_pattern(search)
            cache_keys = list(self._cache.get_client().scan_iter(match=pattern))
            keys_without_prefix = [key.decode("utf-8").split(":", 2)[-1] for key in cache_keys]
            keys_without_prefix.sort()
            return keys_without_prefix
        except Exception as exc:
            LogInfo.warning("Error getting cache keys: %s", str(exc))
            return []

    def _make_pattern(self, search: str | None = None) -> str:
        if search is None:
            search = ""
        version = self.version or ""
        key_prefix = self.key_prefix or ""
        pattern_parts = []

        if key_prefix or version:
            pattern_parts.append("")

        if key_prefix:
            pattern_parts.append(key_prefix)
        if version:
            pattern_parts.append(str(version))

        pattern_parts.append(f"{search}*")
        pattern = ":".join(pattern_parts)
        return pattern


def generate_user_cache_key(request: Any) -> str:
    if request.user.is_authenticated and request.session.session_key:
        return f"{USER_AUTHENTICATED}{request.user.id}:{request.session.session_key}"
    return f"{USER_UNAUTHENTICATED}{request.session.session_key}"


REDIS_HOST = getenv("REDIS_HOST", "localhost")
REDIS_PORT = getenv("REDIS_PORT", "6379")
REDIS_URL = f"redis://{REDIS_HOST}:{REDIS_PORT}/0"
cache_instance = CustomCache(server=REDIS_URL, params={})
