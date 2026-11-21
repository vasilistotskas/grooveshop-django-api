"""One in-app notification per dispatch, however many copies run.

A task queued through ``dispatch_on_commit`` can run more than once (a
retry, a message delivered again, the sweep's copy of a publish), and every
copy carries the task id the helper chose. ``create_user_notification``
keys the row on that id and on what the notification is about (the user,
the type, the link and ``distinct_by``), so the second copy gets back the
first copy's row instead of writing another.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.db import IntegrityError

from core import celery_app
from notification.enum import NotificationTypeEnum
from notification.models.notification import Notification
from notification.models.user import NotificationUser
from notification.services import create_user_notification
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

COPY = {"en": {"title": "Shipped", "message": "On its way"}}


# Registered once, here: a task defined inside a fixture is a new callable on
# every call, and every Celery app finalized later in the run (the beat and
# schedule tests build their own) registers each one under the same name and
# warns ``DuplicateTaskNameWarning``.
@celery_app.task(bind=True, name="tests.notification_per_dispatch")
def notifying_task(self, user_id: int, entries: list[list]) -> list[int | None]:
    """Writes the notifications it is given: one per ``(distinct_by,
    message)`` entry."""
    user = UserAccountFactory._meta.model.objects.get(pk=user_id)
    rows = [
        create_user_notification(
            user,
            notification_type=NotificationTypeEnum.ORDER_SHIPPED,
            link="/account/orders/42",
            translations={"en": {"title": "Hi", "message": message}},
            distinct_by=distinct_by,
        )
        for distinct_by, message in entries
    ]
    return [row.pk if row else None for row in rows]


def run(user, entries, task_id: str) -> list[int | None]:
    return notifying_task.apply(
        [user.pk, entries], task_id=task_id, throw=True
    ).get()


ONE = [[None, "one"]]


def test_a_second_copy_gets_back_the_row_the_first_wrote():
    user = UserAccountFactory()

    first = run(user, ONE, "one-dispatch")
    second = run(user, ONE, "one-dispatch")

    assert first == second
    assert NotificationUser.objects.filter(user=user).count() == 1
    assert Notification.objects.get().dispatch_key is not None


def test_another_dispatch_writes_another_notification():
    user = UserAccountFactory()

    run(user, ONE, "one-dispatch")
    run(user, ONE, "another-dispatch")

    assert NotificationUser.objects.filter(user=user).count() == 2


def test_one_task_can_write_two_notifications_told_apart():
    """Two likes of one comment: same owner, type and link, two likers."""
    user = UserAccountFactory()
    likes = [[1, "liked by a"], [2, "liked by b"]]

    rows = run(user, likes, "dispatch")
    again = run(user, likes, "dispatch")

    assert rows == again
    assert len(set(rows)) == 2
    assert NotificationUser.objects.filter(user=user).count() == 2


def test_a_copy_that_words_it_differently_gets_back_the_first_row():
    """The product was renamed between the copies: still one notification."""
    user = UserAccountFactory()

    first = run(user, [[None, "Mug"]], "one-dispatch")
    second = run(user, [[None, "Big mug"]], "one-dispatch")

    assert first == second
    assert NotificationUser.objects.filter(user=user).count() == 1


def test_a_copy_after_the_user_removed_it_writes_nothing():
    """The user dismissed the notification between the copies: the copy
    neither fails nor brings it back."""
    user = UserAccountFactory()
    run(user, ONE, "one-dispatch")
    NotificationUser.objects.filter(user=user).delete()

    assert run(user, ONE, "one-dispatch") == [None]
    assert not NotificationUser.objects.filter(user=user).exists()


def test_the_push_is_queued_once():
    user = UserAccountFactory()

    with patch("notification.signals.dispatch_on_commit") as queued:
        run(user, ONE, "one-dispatch")
        run(user, ONE, "one-dispatch")

    queued.assert_called_once()


def test_outside_a_task_every_call_writes_a_notification():
    user = UserAccountFactory()

    create_user_notification(user, translations=COPY)
    create_user_notification(user, translations=COPY)

    assert NotificationUser.objects.filter(user=user).count() == 2
    assert not Notification.objects.filter(dispatch_key__isnull=False).exists()


def test_another_integrity_error_is_raised_as_it_is():
    """Only a row holding the key makes a failed insert a copy: any other
    violation is the real failure, never a missing row or a silent None."""
    user = UserAccountFactory()

    with (
        patch(
            "notification.services.Notification.objects.create",
            side_effect=IntegrityError("another constraint"),
        ),
        pytest.raises(IntegrityError, match="another constraint"),
    ):
        run(user, ONE, "one-dispatch")
