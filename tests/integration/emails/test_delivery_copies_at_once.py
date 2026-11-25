"""Two copies of one task sending at the same time send one message.

The message a publish wrote before it failed and the sweep's copy of that
publish are delivered together once the broker takes messages again, so two
workers can run the same task, with the same id, at the same moment. Each
copy runs here on its own thread and database connection, as two workers
would (``tests.utils.concurrency``), and the transport holds the first copy
mid-send.
"""

from __future__ import annotations

import pytest
from django.core import mail
from django.db import OperationalError, connection

from emails.models import EmailLog
from emails.send import Status
from emails.types import Audience, Owner
from tests.utils.concurrency import in_thread, wait_for_a_blocked_backend
from tests.utils.email_tasks import send_once
from tests.utils.emails import HeldTransport, probe

pytestmark = pytest.mark.django_db(transaction=True)

WAIT = 10
DISPATCH = "one-dispatch"


@pytest.fixture
def sending_task(catalogue):
    """A task that sends one platform mail: a platform type needs no store
    bound on the thread that runs it."""
    catalogue(probe(owner=Owner.PLATFORM, audience=Audience.PLATFORM_OPERATOR))
    return send_once


@pytest.fixture(autouse=True)
def _outbox():
    mail.outbox.clear()


def copy_of(task, failures: list[BaseException]):
    return in_thread(lambda: task.apply(task_id=DISPATCH, throw=True), failures)


def test_a_copy_that_starts_while_another_sends_waits_and_sends_nothing(
    sending_task,
):
    transport = HeldTransport(WAIT)
    failures: list[BaseException] = []

    with transport.patched():
        first = copy_of(sending_task, failures)
        assert transport.sending.wait(WAIT)
        second = copy_of(sending_task, failures)
        wait_for_a_blocked_backend(WAIT)
        assert len(transport.calls) == 1

        transport.release.set()
        first.finish(WAIT)
        second.finish(WAIT)

    assert not failures, failures
    assert len(transport.calls) == 1
    assert len(mail.outbox) == 1
    [row] = EmailLog.objects.all()
    assert row.status == Status.SENT


def test_a_copy_waiting_on_a_worker_that_died_sends_it(sending_task):
    """The lock is the session's, so it ends with the worker that held it,
    and the copy that waited sends what the dead one may never have."""
    transport = HeldTransport(WAIT)
    failures: list[BaseException] = []

    with transport.patched():
        first = copy_of(sending_task, failures)
        assert transport.sending.wait(WAIT)
        second = copy_of(sending_task, failures)
        wait_for_a_blocked_backend(WAIT)

        [dead] = transport.calls
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_terminate_backend(%s)", [dead])
        transport.release.set()
        first.finish(WAIT)
        second.finish(WAIT)

    assert not failures, failures
    assert len(transport.calls) == 2
    [row] = EmailLog.objects.all()
    assert row.status == Status.SENT


@pytest.mark.parametrize(
    "wait_bound",
    [
        "SET lock_timeout = '200ms'",
        "SET lock_timeout = 0; SET statement_timeout = '200ms'",
    ],
    ids=["lock_timeout", "statement_timeout"],
)
def test_a_copy_whose_wait_runs_out_raises_and_sends_nothing(
    sending_task, wait_bound
):
    """On a real session: the wait ends as PostgreSQL ends it, and the copy
    raises for its retry instead of sending beside the one ahead."""
    transport = HeldTransport(WAIT)
    failures: list[BaseException] = []

    def bounded_copy() -> None:
        with connection.cursor() as cursor:
            cursor.execute(wait_bound)
        sending_task.apply(task_id=DISPATCH, throw=True)

    with transport.patched():
        first = copy_of(sending_task, [])
        assert transport.sending.wait(WAIT)
        second = in_thread(bounded_copy, failures)
        second.finish(WAIT)
        assert len(transport.calls) == 1

        transport.release.set()
        first.finish(WAIT)

    [failure] = failures
    assert isinstance(failure, OperationalError)
    assert len(mail.outbox) == 1
