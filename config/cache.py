from os import getenv

SYSTEM_ENV = getenv("SYSTEM_ENV", "dev")

if SYSTEM_ENV != "GITHUB_WORKFLOW" or SYSTEM_ENV != "dev":
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": SYSTEM_ENV == "docker"
            and "redis://redis:6379/1"
            or "redis://localhost:6379/1",
            "KEY_PREFIX": "redis",
        },
        "fallback": {
            "BACKEND": "django.core.cache.backends.dummy.DummyCache",
        },
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "unique-snowflake",
            "KEY_PREFIX": "locmem",
        },
        "fallback": {
            "BACKEND": "django.core.cache.backends.dummy.DummyCache",
        },
    }
