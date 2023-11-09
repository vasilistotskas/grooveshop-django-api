from os import getenv

SYSTEM_ENV = getenv("SYSTEM_ENV", "dev")
REDIS_HOST = getenv("REDIS_HOST", "localhost")
REDIS_PORT = getenv("REDIS_PORT", "6379")
REDIS_URL = f"redis://{REDIS_HOST}:{REDIS_PORT}/0"

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
            "LOCATION": "redis://127.0.0.1:6379/0",
            "KEY_PREFIX": "redis",
        },
        "fallback": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "unique-snowflake",
            "KEY_PREFIX": "locmem",
        },
    }
