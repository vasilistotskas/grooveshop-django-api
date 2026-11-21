"""Two copies of one task notifying at the same moment write one row.

The message a publish wrote before it failed and the sweep's copy of that
publish are delivered together once the broker takes messages again. Each
copy runs here on its own thread and database connection, as two workers
would (``tests.utils.concurrency``): the first is held after its insert,
inside its transaction, and the second's insert waits on the unique dispatch
key until the first commits.
"""

from __future__ import annotations

import threading
from unittest import mock

import pytest

from core import celery_app
from notification import services
from notification.models.user import NotificationUser
from notification.services import create_user_notification
from tests.utils.concurrency import in_thread, wait_for_a_blocked_backend
from user.factories.account import UserAccountFactory
from user.models.account import UserAccount

pytestmark = pytest.mark.django_db(transaction=True)

WAIT = 10


@pytest.fixture
def notifying_task():
    @celery_app.task(bind=True, name="tests.notification_copies")
    def task(self, user_id: int) -> int:
        return create_user_notification(
            UserAccount.objects.get(pk=user_id),
            translations={"en": {"title": "Hi", "message": "Once"}},
        ).pk

    yield task
    celery_app.tasks.pop("tests.notification_copies", None)


def test_a_copy_writing_at_the_same_moment_gets_the_first_ones_row(
    notifying_task,
):
    user = UserAccountFactory(num_addresses=0)
    inserted = threading.Event()
    release = threading.Event()
    held = threading.local()
    rows: list[int] = []
    failures: list[BaseException] = []
    real_languages = services.supported_notification_languages

    def held_after_the_insert() -> list[str]:
        if getattr(held, "first", False):
            inserted.set()
            assert release.wait(WAIT)
        return real_languages()

    def copy(first: bool):
        def run() -> None:
            held.first = first
            rows.append(
                notifying_task.apply(
                    [user.pk], task_id="one-dispatch", throw=True
                ).result
            )

        return in_thread(run, failures)

    with mock.patch.object(
        services, "supported_notification_languages", held_after_the_insert
    ):
        first = copy(first=True)
        assert inserted.wait(WAIT)
        second = copy(first=False)
        wait_for_a_blocked_backend(WAIT)

        release.set()
        first.finish(WAIT)
        second.finish(WAIT)

    assert not failures, failures
    assert len(rows) == 2 and rows[0] == rows[1]
    assert NotificationUser.objects.filter(user=user).count() == 1
