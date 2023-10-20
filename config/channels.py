from os import getenv

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [("localhost", 6379)]
            if getenv("SYSTEM_ENV", "development") != "docker"
            else [("redis", 6379)],
        },
    },
}
