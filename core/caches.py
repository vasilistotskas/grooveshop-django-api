from __future__ import annotations

import os
from os import getenv
from typing import Any
from typing import override

import redis
from django.core.cache.backends.redis import RedisCache

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
    def __init__(self, server, params):
        super().__init__(server, params)
        try:
            self._cache.get_client().ping()
        except (redis.exceptions.ConnectionError, Exception) as exc:
            LogInfo.error("Error connecting to cache: %s", str(exc))

    @override
    def get(self, key: Any, default: Any | None = None, version: int | None = None) -> Any | None:
        try:
            return super().get(key, default, version)
        except Exception as exc:
            LogInfo.error("Error getting cache key: %s", str(exc))
            return default

    @override
    def get_many(self, keys: list[Any], version: int | None = None) -> dict[Any, Any | None]:
        try:
            return super().get_many(keys, version)
        except Exception as exc:
            LogInfo.error("Error getting cache keys: %s", str(exc))
            return {}

    @override
    def set(
        self,
        key: Any,
        value: Any,
        timeout: float | None = None,
        version: int | None = None,
    ) -> None:
        try:
            return super().set(key, value, timeout, version)
        except Exception as exc:
            LogInfo.warning("Error setting cache key: %s", str(exc))

    @override
    def get_or_set(
        self,
        key: Any,
        default: Any | None = None,
        timeout: float | None = None,
        version: int | None = None,
    ) -> Any | None:
        try:
            return super().get_or_set(key, default, timeout, version)
        except Exception as exc:
            LogInfo.warning("Error getting or setting cache key: %s", str(exc))
            return default()

    @override
    def add(
        self,
        key: Any,
        value: Any,
        timeout: float | None = None,
        version: int | None = None,
    ) -> bool:
        try:
            return super().add(key, value, timeout, version)
        except Exception as exc:
            LogInfo.warning("Error adding cache key: %s", str(exc))
            return False

    @override
    def delete(self, key: Any, version: int | None = None) -> bool:
        try:
            return super().delete(key, version)
        except Exception as exc:
            LogInfo.warning("Error deleting cache key: %s", str(exc))
            return False

    @override
    def clear(self) -> None:
        try:
            return super().clear()
        except Exception as exc:
            LogInfo.warning("Error clearing cache: %s", str(exc))

    @override
    def has_key(self, key: Any, version: int | None = None) -> bool:
        try:
            return super().has_key(key, version)
        except Exception as exc:
            LogInfo.warning("Error checking cache key: %s", str(exc))
            return False

    @override
    def set_many(
        self,
        data: dict[Any, Any],
        timeout: float | None = None,
        version: int | None = None,
    ) -> list[Any]:
        try:
            return super().set_many(data, timeout, version)
        except Exception as exc:
            LogInfo.warning("Error setting cache keys: %s", str(exc))
            return []

    @override
    def delete_many(self, keys: list[Any], version: int | None = None) -> None:
        try:
            return super().delete_many(keys, version)
        except Exception as exc:
            LogInfo.warning("Error deleting cache keys: %s", str(exc))

    def keys(self, search: str | None = None) -> list[Any]:
        try:
            cache_keys = self._cache.get_client().keys(f"*{search or ''}*")
            keys_without_prefix = [key.decode("utf-8") for key in cache_keys]
            keys_without_prefix.sort()
            return keys_without_prefix
        except Exception as exc:
            LogInfo.warning("Error getting cache keys: %s", str(exc))
            return []


def generate_user_cache_key(request: Any) -> str:
    if request.user.is_authenticated and request.session.session_key:
        return f"{USER_AUTHENTICATED}{request.user.id}:{request.session.session_key}"
    return f"{USER_UNAUTHENTICATED}{request.session.session_key}"


REDIS_HOST = getenv("REDIS_HOST", "localhost")
REDIS_PORT = getenv("REDIS_PORT", "6379")
REDIS_URL = f"redis://{REDIS_HOST}:{REDIS_PORT}/0"
cache_instance = CustomCache(server=REDIS_URL, params={})
