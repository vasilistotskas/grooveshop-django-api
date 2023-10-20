from os import getenv

from core.utils.cache import CustomCacheConfig

SYSTEM_ENV = getenv("SYSTEM_ENV", "dev")

custom_cache_config = CustomCacheConfig()

if SYSTEM_ENV != "GITHUB_WORKFLOW":
    CACHES = {
        "default": custom_cache_config.cache_backend,
        "fallback": {
            "BACKEND": "django.core.cache.backends.dummy.DummyCache",
        },
    }

REDIS_HEALTHY = custom_cache_config.ready_healthy
