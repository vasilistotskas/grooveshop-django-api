import os

import redis


class CustomCacheConfig:
    ready_healthy: bool = False
    cache_backend: dict
    redis_location: str

    def __init__(self):
        self.redis_location = self.redis_location()
        self.ready_healthy = self.is_redis_healthy()
        self.cache_backend = self.get_cache_backend()

    def redis_location(self) -> str:
        if os.getenv("SYSTEM_ENV", "development") == "docker":
            return "redis://redis:6379/0"
        else:
            return os.getenv("REDIS_URL", "redis://localhost:6379/0")

    def is_redis_healthy(self, redis_host="localhost") -> bool:
        try:
            r = redis.Redis(host=redis_host, socket_connect_timeout=60)
            r.ping()
            return True
        except (redis.ConnectionError, redis.TimeoutError):
            return False

    def get_cache_backend(self) -> dict:
        if self.ready_healthy:
            return {
                "BACKEND": "django.core.cache.backends.redis.RedisCache",
                "LOCATION": self.redis_location,
                "KEY_PREFIX": "redis",
            }
        else:
            return {
                "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                "LOCATION": "unique-snowflake",
                "KEY_PREFIX": "locmem",
            }
