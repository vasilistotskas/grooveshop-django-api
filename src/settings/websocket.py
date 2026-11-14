"""The channel layer and the deadline of a push to a WebSocket."""

from os import getenv

from channels_redis.core import RedisChannelLayer

from settings.cache import REDIS_URL

# channels-redis waits for a message with BZPOPMIN for ``brpop_timeout``
# seconds, and redis-py 8 gives every connection a socket timeout of the
# same 5 seconds, so an idle receive raised TimeoutError and closed the
# consumer (django/channels_redis#422). The read timeout must outlast the
# blocking wait; twice that still notices a dead connection.
CHANNEL_LAYER_SOCKET_TIMEOUT = RedisChannelLayer.brpop_timeout * 2

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [
                {
                    "address": REDIS_URL,
                    "socket_timeout": CHANNEL_LAYER_SOCKET_TIMEOUT,
                }
            ],
            "capacity": 1500,
            "expiry": 300,
        },
    },
}

# A push to a user's WebSocket group is best effort, so a sender gives up
# after this many seconds and leaves the rest to its caller (the task's
# retry). The socket timeout above cannot serve as that deadline: it is
# sized for the consumer's blocking receive, and it bounds each Redis
# command, of which one ``group_send`` issues up to four.
NOTIFICATION_PUSH_TIMEOUT_SECONDS = float(
    getenv("NOTIFICATION_PUSH_TIMEOUT_SECONDS", "5")
)
# ``send_notification_task`` does nothing but that push, so it needs no
# more than twice its deadline. The soft limit catches a send the deadline
# could not interrupt; the hard limit ends a worker that ignored both.
NOTIFICATION_TASK_SOFT_TIME_LIMIT = NOTIFICATION_PUSH_TIMEOUT_SECONDS * 2
NOTIFICATION_TASK_TIME_LIMIT = NOTIFICATION_PUSH_TIMEOUT_SECONDS * 3
