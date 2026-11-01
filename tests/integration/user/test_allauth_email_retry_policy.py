"""When a mail send fails, retry only what a retry could fix.

`send_allauth_email_task` moves the SMTP conversation out of the
request so a transient 421 stops being a 500 on signup. That leaves two
things to get right, and RFC 5321 decides the first: 4xx means "try
again", 5xx means "do not". The exception CLASS cannot express it —
`SMTPConnectError(421)` and `SMTPAuthenticationError(535)` are both
`SMTPResponseException`.

The second is what reaches the logs. The task's payload is the context of
the mail, and for a password reset or a login code that payload IS the
secret.
"""

from __future__ import annotations

import smtplib
from unittest.mock import patch

import pytest
from celery import Task
from celery.exceptions import Retry
from django.core import mail

from core.retry import is_permanent_smtp_failure, smtp_codes
from user.tasks import send_allauth_email_task

MESSAGE = {
    "type_key": "account.login_code",
    "address": "person@example.test",
    "language": "en",
    "user_pk": None,
    "context": {"code": "ONE-TIME-TOKEN"},
}


class TestFailureClassification:
    @pytest.mark.parametrize(
        ("exc", "permanent"),
        [
            # The production incident: "421 Server busy, try again later".
            (smtplib.SMTPConnectError(421, b"4.4.5 Server busy"), False),
            (smtplib.SMTPDataError(451, b"4.3.0 try later"), False),
            # Bad credentials. Five retries with backoff is how a
            # provider locks the account.
            (smtplib.SMTPAuthenticationError(535, b"5.7.8 bad creds"), True),
            (
                smtplib.SMTPSenderRefused(550, b"5.1.8 bad sender", "a@b.c"),
                True,
            ),
            (smtplib.SMTPDataError(552, b"5.3.4 message too big"), True),
            # No code at all — the conversation never got that far.
            (smtplib.SMTPServerDisconnected("connection lost"), False),
            (TimeoutError("timed out"), False),
            (ConnectionRefusedError("refused"), False),
            # The server cannot do what we asked; asking again will not
            # change that.
            (smtplib.SMTPNotSupportedError("no SMTPUTF8"), True),
        ],
    )
    def test_permanence_follows_the_smtp_code(self, exc, permanent):
        assert is_permanent_smtp_failure(exc) is permanent

    def test_all_refused_recipients_are_read_from_the_mapping(self):
        """``SMTPRecipientsRefused`` carries codes per address, not on
        ``smtp_code``, and is only raised when EVERY recipient failed."""
        refused = smtplib.SMTPRecipientsRefused(
            {"gone@example.test": (550, b"5.1.1 no such user")}
        )
        assert smtp_codes(refused) == [550]
        assert is_permanent_smtp_failure(refused) is True

        deferred = smtplib.SMTPRecipientsRefused(
            {"busy@example.test": (451, b"4.3.2 try later")}
        )
        assert is_permanent_smtp_failure(deferred) is False


@pytest.mark.django_db
class TestRetryBehaviour:
    def test_a_transient_failure_is_retried(self):
        with (
            patch.object(
                mail.EmailMultiAlternatives,
                "send",
                side_effect=smtplib.SMTPConnectError(421, b"Server busy"),
            ),
            # ``Task.retry`` signals the worker by RAISING ``Retry``;
            # a bare mock returns one instead, and ``raise`` then fails
            # on a non-exception.
            patch.object(
                send_allauth_email_task, "retry", side_effect=Retry()
            ) as retry,
            pytest.raises(Retry),
        ):
            send_allauth_email_task(**MESSAGE)

        assert retry.called, "a 421 must be retried — that is the whole fix"

    def test_a_permanent_failure_is_not_retried(self):
        """Burning five backed-off retries on a 5xx only delays the
        failure record; nothing about the outcome changes."""
        with (
            patch.object(
                mail.EmailMultiAlternatives,
                "send",
                side_effect=smtplib.SMTPSenderRefused(
                    550, b"5.1.8 bad sender", "noreply@example.test"
                ),
            ),
            # The class-level patch sees the call the instance method
            # forwards; ``MonitoredTask.retry`` must raise before it.
            patch.object(Task, "retry") as retry,
            pytest.raises(smtplib.SMTPSenderRefused),
        ):
            send_allauth_email_task(**MESSAGE)

        assert not retry.called, "a 5xx was retried"


class TestTheContextNeverReachesTheLogs:
    def test_the_context_is_masked_on_failure(self, caplog):
        """The failure path logs the task's kwargs. For a login code or a
        password reset that kwarg carries the one-time secret."""
        send_allauth_email_task.on_failure(
            smtplib.SMTPConnectError(421, b"Server busy"),
            "task-id",
            [],
            dict(MESSAGE),
            None,
        )

        logged = caplog.text
        assert "ONE-TIME-TOKEN" not in logged, (
            "the one-time code reached the log"
        )
        assert "***" in logged, "the context was not masked"
        # The recipient stays: it is what makes a delivery failure
        # actionable, and it is not a credential.
        assert "person@example.test" in logged
