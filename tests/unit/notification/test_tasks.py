"""``send_notification_task`` pushes within a deadline (#648).

The push is best effort, so a channel layer that never answers must
cost the worker the configured deadline and then take the task's retry
path, not hold the worker until Celery's global soft limit.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import threading
import time
from unittest.mock import patch

import pytest
from asgiref.sync import async_to_sync
from celery.exceptions import Retry
from channels.layers import InMemoryChannelLayer
from channels_redis.core import RedisChannelLayer
from django.conf import settings
from django.test import override_settings

from notification.groups import send_to_user_group, user_group
from notification.tasks import send_notification_task

# Short enough to keep the tests fast; ``timeout`` bounds a test should
# the send hang again.
DEADLINE = 0.05


class NeverAnsweringLayer(InMemoryChannelLayer):
    """A layer whose ``group_send`` waits for a reply that never comes."""

    def __init__(self) -> None:
        super().__init__()
        self.group_sends = 0

    async def group_send(self, group, message):
        self.group_sends += 1
        await asyncio.Event().wait()


@pytest.mark.timeout(30)
@override_settings(NOTIFICATION_PUSH_TIMEOUT_SECONDS=DEADLINE)
def test_a_send_that_never_returns_times_out_and_is_retried():
    layer = NeverAnsweringLayer()

    # Eagerly applied, a retry surfaces as the ``Retry`` it raises.
    with (
        patch("notification.tasks.get_channel_layer", return_value=layer),
        pytest.raises(Retry) as retry,
    ):
        send_notification_task.apply(args=[{"user_id": 1}])

    assert isinstance(retry.value.exc, TimeoutError)
    assert layer.group_sends == 1


@pytest.mark.timeout(30)
@override_settings(NOTIFICATION_PUSH_TIMEOUT_SECONDS=DEADLINE)
def test_the_last_attempt_that_times_out_fails_the_task():
    layer = NeverAnsweringLayer()

    with (
        patch("notification.tasks.get_channel_layer", return_value=layer),
        pytest.raises(TimeoutError),
    ):
        send_notification_task.apply(
            args=[{"user_id": 1}],
            retries=send_notification_task.max_retries,
        )

    assert layer.group_sends == 1


@pytest.fixture
def silent_redis_url():
    """A TCP server that accepts connections and never answers.

    What a paused or wedged Redis looks like to a client: the kernel
    completes the handshake, and no reply ever comes.
    """
    server = socket.create_server(("127.0.0.1", 0))
    accepted: list[socket.socket] = []

    def accept() -> None:
        with contextlib.suppress(OSError):
            while True:
                accepted.append(server.accept()[0])

    threading.Thread(target=accept, daemon=True).start()
    yield f"redis://127.0.0.1:{server.getsockname()[1]}/0"
    server.close()
    for conn in accepted:
        conn.close()


@pytest.mark.timeout(30)
@override_settings(NOTIFICATION_PUSH_TIMEOUT_SECONDS=DEADLINE)
def test_the_deadline_ends_a_redis_send_before_its_socket_timeout(
    silent_redis_url,
):
    layer = RedisChannelLayer(
        hosts=[
            {
                "address": silent_redis_url,
                "socket_timeout": settings.CHANNEL_LAYER_SOCKET_TIMEOUT,
            }
        ]
    )

    started = time.monotonic()
    with pytest.raises(TimeoutError):
        send_to_user_group(layer, 1, {"type": "send_notification"})

    assert time.monotonic() - started < settings.CHANNEL_LAYER_SOCKET_TIMEOUT


@pytest.mark.timeout(30)
def test_a_send_that_answers_reaches_the_user_group():
    layer = InMemoryChannelLayer()
    channel = async_to_sync(layer.new_channel)()
    async_to_sync(layer.group_add)(user_group("public", 7), channel)

    with patch("notification.tasks.get_channel_layer", return_value=layer):
        send_notification_task.apply(args=[{"user_id": 7, "id": 3}])

    message = async_to_sync(layer.receive)(channel)
    assert message["type"] == "send_notification"
    assert message["user"] == 7
    assert message["id"] == 3


def test_the_task_has_its_own_time_limits():
    assert (
        send_notification_task.soft_time_limit
        == settings.NOTIFICATION_TASK_SOFT_TIME_LIMIT
    )
    assert (
        send_notification_task.time_limit
        == settings.NOTIFICATION_TASK_TIME_LIMIT
    )
    assert (
        settings.NOTIFICATION_PUSH_TIMEOUT_SECONDS
        < settings.NOTIFICATION_TASK_SOFT_TIME_LIMIT
        < settings.NOTIFICATION_TASK_TIME_LIMIT
    )
