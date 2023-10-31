from __future__ import annotations

import logging
import os
from typing import Any

import redis
from django.core.cache import BaseCache
from django.core.cache import caches
from django.core.cache.backends.locmem import LocMemCache
from django.core.cache.backends.redis import RedisCache

logger = logging.getLogger(__name__)

# Constants

DEFAULT_CACHE_ALIAS = "default"
FALLBACK_CACHE_ALIAS = "fallback"

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

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "app.settings")


class CustomCache(BaseCache):
    cache: BaseCache

    def __init__(self, params: dict[str, Any]):
        super().__init__(params)
        try:
            self.cache = caches[DEFAULT_CACHE_ALIAS]
            if isinstance(self.cache, RedisCache):
                self.cache._cache.get_client().ping()
        except (redis.exceptions.ConnectionError, Exception) as exc:
            logger.warning("Error connecting to cache: %s", str(exc))
            self.cache = caches[FALLBACK_CACHE_ALIAS]

    def get(
        self, key: Any, default: Any | None = None, version: int | None = None
    ) -> Any | None:
        try:
            return self.cache.get(key, default, version)
        except Exception as exc:
            logger.error("Error getting cache key: %s", str(exc))
            return default

    def get_many(
        self, keys: list[Any], version: int | None = None
    ) -> dict[Any, Any | None]:
        try:
            return self.cache.get_many(keys, version)
        except Exception as exc:
            logger.error("Error getting cache keys: %s", str(exc))
            return {}

    def set(
        self,
        key: Any,
        value: Any,
        timeout: float | None = None,
        version: int | None = None,
    ) -> None:
        try:
            return self.cache.set(key, value, timeout, version)
        except Exception as exc:
            logger.warning("Error setting cache key: %s", str(exc))

    def get_or_set(
        self,
        key: Any,
        default: Any | None = None,
        timeout: float | None = None,
        version: int | None = None,
    ) -> Any | None:
        try:
            return self.cache.get_or_set(key, default, timeout, version)
        except Exception as exc:
            logger.warning("Error getting or setting cache key: %s", str(exc))
            return default()

    def add(
        self,
        key: Any,
        value: Any,
        timeout: float | None = None,
        version: int | None = None,
    ) -> bool:
        try:
            return self.cache.add(key, value, timeout, version)
        except Exception as exc:
            logger.warning("Error adding cache key: %s", str(exc))
            return False

    def delete(self, key: Any, version: int | None = None) -> bool:
        try:
            return self.cache.delete(key, version)
        except Exception as exc:
            logger.warning("Error deleting cache key: %s", str(exc))
            return False

    def clear(self) -> None:
        try:
            self.cache.clear()
        except Exception as exc:
            logger.warning("Error clearing cache: %s", str(exc))

    def has_key(self, key: Any, version: int | None = None) -> bool:
        try:
            return self.cache.has_key(key, version)
        except Exception as exc:
            logger.warning("Error checking cache key: %s", str(exc))
            return False

    def set_many(
        self,
        data: dict[Any, Any],
        timeout: float | None = None,
        version: int | None = None,
    ) -> list[Any]:
        try:
            return self.cache.set_many(data, timeout, version)
        except Exception as exc:
            logger.warning("Error setting cache keys: %s", str(exc))
            return []

    def delete_many(self, keys: list[Any], version: int | None = None) -> None:
        try:
            return self.cache.delete_many(keys, version)
        except Exception as exc:
            logger.warning("Error deleting cache keys: %s", str(exc))

    def keys(self, search: str | None = None) -> list[Any]:
        try:
            if isinstance(self.cache, RedisCache):
                cache_keys = self.cache._cache.get_client().keys(f"*{search or ''}*")
                keys_without_prefix = [
                    key.split(b":", 2)[-1].decode("utf-8") for key in cache_keys
                ]
                keys_without_prefix.sort()
                return keys_without_prefix
            elif isinstance(self.cache, LocMemCache):
                cache_keys = list(self.cache._cache.keys())
                keys_without_prefix = [
                    key.split(":", 2)[-1] for key in cache_keys if "locmem:1:" in key
                ]
                keys_without_prefix.sort()
                filtered_keys = [
                    key for key in keys_without_prefix if "user_authenticated" in key
                ]
                return filtered_keys

        except Exception as exc:
            logger.warning("Error getting cache keys: %s", str(exc))
            return []


def generate_user_cache_key(request: Any) -> str:
    if request.user.is_authenticated and request.session.session_key:
        return f"{USER_AUTHENTICATED}{request.user.id}:{request.session.session_key}"
    return f"{USER_UNAUTHENTICATED}{request.session.session_key}"


cache_instance = CustomCache(params={})
