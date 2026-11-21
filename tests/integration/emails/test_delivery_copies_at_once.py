"""Two copies of one task sending at the same time send one message.

The message a publish wrote before it failed and the sweep's copy of that
publish are delivered together once the broker takes messages again, so two
workers can run the same task, with the same id, at the same moment. Each
copy runs here on its own thread and database connection, as two workers
would, and the transport holds the first copy mid-send.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from unittest import mock

import pytest
from django.core import mail
from django.db import connection

from core import celery_app
from emails.models import EmailLog
from emails.send import Status, send_email
from emails.types import Audience, Owner
from tests.utils.emails import probe, probe_object

pytestmark = pytest.mark.django_db(transaction=True)

WAIT = 10
DISPATCH = "one-dispatch"


@pytest.fixture
def sending_task(catalogue):
    """A task that sends one platform mail: a platform type needs no store
    bound on the thread that runs it."""
    catalogue(probe(owner=Owner.PLATFORM, audience=Audience.PLATFORM_OPERATOR))

    @celery_app.task(bind=True, name="tests.delivery_copies")
    def task(self):
        send_email("probe.notice", probe_object())

    yield task
    celery_app.tasks.pop("tests.delivery_copies", None)


@pytest.fixture(autouse=True)
def _outbox():
    mail.outbox.clear()


class HeldTransport:
    """The SMTP send, held at its start until ``release`` is set."""

    def __init__(self) -> None:
        self.calls: list[int] = []
        self.sending = threading.Event()
        self.release = threading.Event()
        self.real = mail.EmailMultiAlternatives.send

    def __call__(self, message, *args, **kwargs):
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_backend_pid()")
            (pid,) = cursor.fetchone()
        self.calls.append(pid)
        self.sending.set()
        assert self.release.wait(WAIT)
        return self.real(message, *args, **kwargs)


def run_copy(task, errors: list[BaseException]) -> threading.Thread:
    def run() -> None:
        try:
            task.apply(task_id=DISPATCH, throw=True)
        except BaseException as error:
            errors.append(error)
        finally:
            connection.close()

    thread = threading.Thread(target=run)
    thread.start()
    return thread


def waiting_for_a_lock() -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
            "AND NOT granted AND database = "
            "(SELECT oid FROM pg_database WHERE datname = current_database())"
        )
        (waiting,) = cursor.fetchone()
    return waiting


def eventually(condition: Callable[[], bool]) -> None:
    deadline = time.monotonic() + WAIT
    while not condition():
        assert time.monotonic() < deadline, "the condition never held"
        time.sleep(0.05)


def test_a_copy_that_starts_while_another_sends_waits_and_sends_nothing(
    sending_task,
):
    transport = HeldTransport()
    errors: list[BaseException] = []

    with mock.patch("emails.transport.EmailMultiAlternatives.send", transport):
        first = run_copy(sending_task, errors)
        assert transport.sending.wait(WAIT)
        second = run_copy(sending_task, errors)
        eventually(lambda: waiting_for_a_lock() == 1)
        assert len(transport.calls) == 1

        transport.release.set()
        first.join(WAIT)
        second.join(WAIT)

    assert not errors, errors
    assert len(transport.calls) == 1
    assert len(mail.outbox) == 1
    [row] = EmailLog.objects.all()
    assert row.status == Status.SENT


def test_a_copy_waiting_on_a_worker_that_died_sends_it(sending_task):
    """The lock is the session's, so it ends with the worker that held it,
    and the copy that waited sends what the dead one may never have."""
    transport = HeldTransport()
    errors: list[BaseException] = []

    with mock.patch("emails.transport.EmailMultiAlternatives.send", transport):
        first = run_copy(sending_task, errors)
        assert transport.sending.wait(WAIT)
        second = run_copy(sending_task, errors)
        eventually(lambda: waiting_for_a_lock() == 1)

        [dead] = transport.calls
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_terminate_backend(%s)", [dead])
        eventually(lambda: len(transport.calls) == 2)

        transport.release.set()
        first.join(WAIT)
        second.join(WAIT)

    assert not errors, errors
    [row] = EmailLog.objects.all()
    assert row.status == Status.SENT
