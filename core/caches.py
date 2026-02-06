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
    Custom Redis cache backend with enhanced key management.

    Provides proper key prefixing to prevent collisions and additional
    utility methods for cache inspection.
    """

    _cache: RedisCacheClient

    def __init__(self, server: str, params: dict) -> None:
        """
        Initialize the custom cache backend.

        Args:
            server: Redis server URL
            params: Cache configuration parameters
        """
        super().__init__(server, params)

        try:
            self._cache.get_client().ping()
            logger.info("Successfully connected to Redis cache")
        except (redis.exceptions.ConnectionError, Exception) as exc:
            logger.error("Error connecting to cache: %s", str(exc))

    def make_key(self, key: str, version: int | None = None) -> str:
        """
        Construct the final cache key with proper prefixing.

        Overrides parent to add custom namespace while maintaining
        Django's key versioning system.

        Args:
            key: Base cache key
            version: Optional version number for the key

        Returns:
            Fully qualified cache key with prefix and version
        """
        # Use parent's make_key to get proper versioning and validation
        final_key = super().make_key(key, version)
        return final_key

    def keys(self, search: str | None = None) -> list[str]:
        """
        Search for cache keys matching a pattern.

        Args:
            search: Optional search pattern (wildcards supported)

        Returns:
            List of matching cache keys (without version prefix), sorted alphabetically
        """
        try:
            pattern = self._make_pattern(search)
            logger.info(f"Searching for keys with pattern: {pattern}")
            cache_keys = list(self._cache.get_client().scan_iter(match=pattern))

            # Decode and remove version prefix to return clean keys
            keys_with_prefix = []
            for key in cache_keys:
                decoded_key = key.decode("utf-8")
                # Remove Django's version prefix (e.g., ":1:")
                if decoded_key.startswith(":"):
                    # Split by ":" and rejoin without the version prefix
                    parts = decoded_key.split(":", 2)
                    if len(parts) >= 3:
                        decoded_key = parts[2]
                keys_with_prefix.append(decoded_key)

            keys_with_prefix.sort()
            logger.info(f"Found {len(keys_with_prefix)} keys")
            return keys_with_prefix
        except Exception as exc:
            logger.warning("Error getting cache keys: %s", str(exc))
            return []

    @staticmethod
    def _make_pattern(search: str | None = None) -> str:
        """
        Create a Redis pattern from a search string.

        Args:
            search: Optional search string

        Returns:
            Redis-compatible pattern string
        """
        if search is None:
            return "*"
        return f"*{search}*"


REDIS_HOST = getenv("REDIS_HOST", "localhost")
REDIS_PORT = getenv("REDIS_PORT", "6379")
REDIS_URL = f"redis://{REDIS_HOST}:{REDIS_PORT}/0"
cache_instance = CustomCache(server=REDIS_URL, params={})
