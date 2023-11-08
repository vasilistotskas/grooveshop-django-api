from os import getenv

SYSTEM_ENV = getenv("SYSTEM_ENV", "dev")
REDIS_URL = getenv("REDIS_URL", "redis://localhost:6379/1")

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
        "KEY_PREFIX": "redis",
    },
    "fallback": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "unique-snowflake",
        "KEY_PREFIX": "locmem",
    },
}

if SYSTEM_ENV == "GITHUB_WORKFLOW":
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": "redis://127.0.0.1:6379/1",
            "KEY_PREFIX": "redis",
        },
        "fallback": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "unique-snowflake",
            "KEY_PREFIX": "locmem",
        },
    }
