"""A delivery has one identity across the retries of its task.

Celery keeps the task id over ``retry()``, so a send repeated after a lost
response is the same delivery: it reuses its ``EmailLog`` row, and a
recipient already sent is never sent again.
"""

from __future__ import annotations

from smtplib import SMTPServerDisconnected
from unittest import mock

import pytest
from django.core import mail
from django.db import OperationalError, ProgrammingError
from psycopg.errors import LockNotAvailable, QueryCanceled

from emails.models import EmailLog
from emails.recipients import Recipient
from emails.send import Status, send_email
from tests.utils.email_tasks import send_retrying as retrying_task
from tests.utils.email_tasks import send_three_deliveries
from tests.utils.emails import probe, probe_object

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _outbox(tenant):
    mail.outbox.clear()


class TestRetry:
    def test_a_retry_after_a_failure_reuses_the_row(self, catalogue):
        catalogue(probe())
        real = mail.EmailMultiAlternatives.send
        calls = []

        def flaky(message, *args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise SMTPServerDisconnected("lost")
            return real(message, *args, **kwargs)

        with mock.patch("emails.transport.EmailMultiAlternatives.send", flaky):
            retrying_task.apply(throw=False)

        assert len(calls) == 2
        [row] = EmailLog.objects.all()
        assert row.status == Status.SENT
        assert row.error == ""
        assert len(mail.outbox) == 1

    def test_a_recipient_already_sent_is_not_sent_again(self, catalogue):
        catalogue(
            probe(
                recipients=lambda obj: [
                    Recipient("a@example.test", "en"),
                    Recipient("b@example.test", "en"),
                ]
            )
        )
        real = mail.EmailMultiAlternatives.send

        def second_fails_once(message, *args, **kwargs):
            if message.to == ["b@example.test"] and not second_fails_once.done:
                second_fails_once.done = True
                raise SMTPServerDisconnected("lost")
            return real(message, *args, **kwargs)

        second_fails_once.done = False

        with mock.patch(
            "emails.transport.EmailMultiAlternatives.send",
            second_fails_once,
        ):
            retrying_task.apply(throw=False)

        assert sorted(m.to[0] for m in mail.outbox) == [
            "a@example.test",
            "b@example.test",
        ]
        assert EmailLog.objects.filter(status=Status.SENT).count() == 2
        assert EmailLog.objects.count() == 2


class TestCopiesOfOneDispatch:
    """``dispatch_on_commit`` gives each dispatch its task id, and every copy
    of it carries that id: the message a failed publish wrote and the
    sweep's copy of it, a message delivered again after a worker was lost."""

    def test_a_second_copy_sends_nothing(self, catalogue):
        catalogue(probe())

        retrying_task.apply(task_id="one-dispatch")
        retrying_task.apply(task_id="one-dispatch")

        [row] = EmailLog.objects.all()
        assert row.status == Status.SENT
        assert len(mail.outbox) == 1

    def test_another_dispatch_of_the_task_is_another_delivery(self, catalogue):
        catalogue(probe())

        retrying_task.apply(task_id="one-dispatch")
        retrying_task.apply(task_id="another-dispatch")

        assert EmailLog.objects.filter(status=Status.SENT).count() == 2
        assert len(mail.outbox) == 2


class TestTheDeliveryLock:
    def test_a_database_out_of_reach_does_not_stop_the_send(
        self, catalogue, caplog
    ):
        """An alert about a failing database must still go out, as it does
        when the row cannot be written."""
        catalogue(probe())
        unreachable = mock.MagicMock(in_atomic_block=False)
        unreachable.cursor.side_effect = OperationalError("unreachable")

        with mock.patch("core.db.locks.connection", unreachable):
            retrying_task.apply(task_id="one-dispatch", throw=True)

        assert len(mail.outbox) == 1
        assert "sent without its lock" in caplog.text

    def test_another_database_error_is_raised_and_sends_nothing(
        self, catalogue
    ):
        """Only a database out of reach lets the send go on unlocked: any
        other error is a bug, and two copies could send without the lock."""
        catalogue(probe())
        broken = mock.MagicMock(in_atomic_block=False)
        broken.cursor.side_effect = ProgrammingError("a bug")

        with (
            mock.patch("core.db.locks.connection", broken),
            pytest.raises(ProgrammingError),
        ):
            retrying_task.apply(task_id="one-dispatch", throw=True)

        assert mail.outbox == []

    @pytest.mark.parametrize("cause", [QueryCanceled, LockNotAvailable])
    def test_a_wait_that_runs_out_is_raised_for_the_retry(
        self, catalogue, cause
    ):
        """``statement_timeout`` or ``lock_timeout`` ended the wait: the copy
        ahead is still sending, so this one must not send too."""
        catalogue(probe())
        waited_out = OperationalError("the wait for the lock ran out")
        waited_out.__cause__ = cause()
        held = mock.MagicMock(in_atomic_block=False)
        held.cursor.return_value.__enter__.return_value.execute.side_effect = (
            waited_out
        )

        with (
            mock.patch("core.db.locks.connection", held),
            pytest.raises(OperationalError),
        ):
            retrying_task.apply(task_id="one-dispatch", throw=True)

        assert mail.outbox == []


class TestIdentity:
    def test_two_synchronous_sends_are_two_deliveries(self, catalogue):
        catalogue(probe())

        send_email("probe.notice", probe_object())
        send_email("probe.notice", probe_object())

        assert EmailLog.objects.count() == 2
        assert len(mail.outbox) == 2

    def test_in_one_task_another_recipient_or_type_is_another_delivery(
        self, catalogue
    ):
        catalogue(probe(), probe("probe.other"))

        send_three_deliveries.apply()

        assert EmailLog.objects.values("uuid").distinct().count() == 3
        assert len(mail.outbox) == 3
