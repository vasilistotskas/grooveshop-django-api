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
from django.db.models.signals import post_save
from django.test import override_settings
from factory.django import mute_signals

from notification.factories.user import NotificationUserFactory
from notification.groups import send_to_user_group, user_group
from notification.models.user import NotificationUser
from notification.tasks import send_notification_task

# Short enough to keep the tests fast; ``timeout`` bounds a test should
# the send hang again.
DEADLINE = 0.05


@pytest.fixture
def row():
    """A notification not pushed yet: the push its save queues is muted."""
    with mute_signals(post_save):
        return NotificationUserFactory()


def push_of(row: NotificationUser) -> dict:
    return {"user_id": row.user_id, "id": row.id}


class CountingLayer(InMemoryChannelLayer):
    def __init__(self) -> None:
        super().__init__()
        self.group_sends = 0

    async def group_send(self, group, message):
        self.group_sends += 1
        await super().group_send(group, message)


class NeverAnsweringLayer(InMemoryChannelLayer):
    """A layer whose ``group_send`` waits for a reply that never comes."""

    def __init__(self) -> None:
        super().__init__()
        self.group_sends = 0

    async def group_send(self, group, message):
        self.group_sends += 1
        await asyncio.Event().wait()


@pytest.mark.django_db
@pytest.mark.timeout(30)
@override_settings(NOTIFICATION_PUSH_TIMEOUT_SECONDS=DEADLINE)
def test_a_send_that_never_returns_times_out_and_is_retried(row):
    layer = NeverAnsweringLayer()

    # Eagerly applied, a retry surfaces as the ``Retry`` it raises.
    with (
        patch("notification.tasks.get_channel_layer", return_value=layer),
        pytest.raises(Retry) as retry,
    ):
        send_notification_task.apply(args=[push_of(row)])

    assert isinstance(retry.value.exc, TimeoutError)
    assert layer.group_sends == 1
    # The claim is given back, so the retry pushes.
    row.refresh_from_db()
    assert row.pushed_at is None


@pytest.mark.django_db
@pytest.mark.timeout(30)
@override_settings(NOTIFICATION_PUSH_TIMEOUT_SECONDS=DEADLINE)
def test_the_last_attempt_that_times_out_fails_the_task(row):
    layer = NeverAnsweringLayer()

    with (
        patch("notification.tasks.get_channel_layer", return_value=layer),
        pytest.raises(TimeoutError),
    ):
        send_notification_task.apply(
            args=[push_of(row)],
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


@pytest.mark.django_db
@pytest.mark.timeout(30)
def test_a_send_that_answers_reaches_the_user_group(row):
    layer = InMemoryChannelLayer()
    channel = async_to_sync(layer.new_channel)()
    async_to_sync(layer.group_add)(user_group("public", row.user_id), channel)

    with patch("notification.tasks.get_channel_layer", return_value=layer):
        send_notification_task.apply(args=[push_of(row)])

    message = async_to_sync(layer.receive)(channel)
    assert message["type"] == "send_notification"
    assert message["user"] == row.user_id
    assert message["id"] == row.id


@pytest.mark.django_db
@pytest.mark.timeout(30)
def test_a_second_copy_of_the_push_pushes_nothing(row, caplog):
    """The storefront toasts every frame: a retry, a message delivered
    again or the sweep's copy of a publish must not push the row twice."""
    layer = CountingLayer()

    with patch("notification.tasks.get_channel_layer", return_value=layer):
        send_notification_task.apply(args=[push_of(row)], task_id="one")
        send_notification_task.apply(args=[push_of(row)], task_id="one")
        send_notification_task.apply(args=[push_of(row)], task_id="another")

    assert layer.group_sends == 1
    row.refresh_from_db()
    assert row.pushed_at is not None
    assert "already pushed" in caplog.text
    assert "is gone" not in caplog.text


@pytest.mark.django_db
def test_a_row_that_is_gone_is_not_pushed_and_says_so(row, caplog):
    """Not logged as a copy: a row missing from the schema the task looks
    in can be a routing bug."""
    layer = CountingLayer()
    data = push_of(row)
    row.delete()

    with patch("notification.tasks.get_channel_layer", return_value=layer):
        send_notification_task.apply(args=[data])

    assert layer.group_sends == 0
    [record] = [r for r in caplog.records if "is gone" in r.getMessage()]
    assert record.levelname == "WARNING"
    assert "already pushed" not in caplog.text


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
