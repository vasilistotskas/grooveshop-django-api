"""One in-app notification per dispatch, however many copies run.

A task queued through ``dispatch_on_commit`` can run more than once (a
retry, a message delivered again, the sweep's copy of a publish), and every
copy carries the task id the helper chose. ``create_user_notification``
keys the row on that id and on what the notification says, so the second
copy gets back the first copy's row instead of writing another.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from core import celery_app
from notification.enum import NotificationTypeEnum
from notification.models.notification import Notification
from notification.models.user import NotificationUser
from notification.services import create_user_notification
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

COPY = {"en": {"title": "Shipped", "message": "On its way"}}


@pytest.fixture
def notifying_task():
    """A task that writes the notifications it is given, one per entry."""

    @celery_app.task(bind=True, name="tests.notification_per_dispatch")
    def task(self, user_id: int, messages: list[str]) -> list[int]:
        user = UserAccountFactory._meta.model.objects.get(pk=user_id)
        return [
            create_user_notification(
                user,
                notification_type=NotificationTypeEnum.ORDER_SHIPPED,
                link="/account/orders/42",
                translations={"en": {"title": "Hi", "message": message}},
            ).pk
            for message in messages
        ]

    yield task
    celery_app.tasks.pop("tests.notification_per_dispatch", None)


def run(task, user, messages, task_id: str) -> list[int]:
    return task.apply([user.pk, messages], task_id=task_id, throw=True).get()


def test_a_second_copy_gets_back_the_row_the_first_wrote(notifying_task):
    user = UserAccountFactory()

    first = run(notifying_task, user, ["one"], "one-dispatch")
    second = run(notifying_task, user, ["one"], "one-dispatch")

    assert first == second
    assert NotificationUser.objects.filter(user=user).count() == 1
    assert Notification.objects.get().dispatch_key is not None


def test_another_dispatch_writes_another_notification(notifying_task):
    user = UserAccountFactory()

    run(notifying_task, user, ["one"], "one-dispatch")
    run(notifying_task, user, ["one"], "another-dispatch")

    assert NotificationUser.objects.filter(user=user).count() == 2


def test_one_task_can_write_two_notifications_to_one_user(notifying_task):
    """Two likes of one comment: same owner, type and link, other words."""
    user = UserAccountFactory()

    rows = run(notifying_task, user, ["liked by a", "liked by b"], "dispatch")
    again = run(notifying_task, user, ["liked by a", "liked by b"], "dispatch")

    assert rows == again
    assert len(set(rows)) == 2
    assert NotificationUser.objects.filter(user=user).count() == 2


def test_the_push_is_queued_once(notifying_task):
    user = UserAccountFactory()

    with patch("notification.signals.dispatch_on_commit") as queued:
        run(notifying_task, user, ["one"], "one-dispatch")
        run(notifying_task, user, ["one"], "one-dispatch")

    queued.assert_called_once()


def test_outside_a_task_every_call_writes_a_notification():
    user = UserAccountFactory()

    create_user_notification(user, translations=COPY)
    create_user_notification(user, translations=COPY)

    assert NotificationUser.objects.filter(user=user).count() == 2
    assert not Notification.objects.filter(dispatch_key__isnull=False).exists()
