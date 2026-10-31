"""allauth mail must not fail the request that triggers it.

Production, 2026-09-15: smtp.gmail.com answered 421 "4.4.5 Server busy,
try again later" and the resulting ``smtplib.SMTPConnectError``
propagated out of ``/_allauth/app/v1/auth/signup`` as a 500. A real
person could not create an account because the mail provider was
briefly rate limiting us.

The adapter queues ``send_allauth_email_task`` and the pipeline renders
and sends from it, where a 421 is a retry rather than a 500.
"""

from __future__ import annotations

import smtplib
from unittest.mock import patch

import pytest
from allauth.core.context import request_context
from celery.exceptions import Retry
from django.contrib.auth.models import AnonymousUser
from django.core import mail
from django.test import RequestFactory

from user.adapter import UserAccountAdapter
from user.factories.account import UserAccountFactory
from user.tasks import send_allauth_email_task


def _send(user):
    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    with request_context(request):
        UserAccountAdapter(request).send_mail(
            "account/email/login_code",
            user.email,
            {"user": user, "code": "123456"},
        )


@pytest.mark.django_db
class TestAllauthMailIsDispatched:
    def test_the_request_never_talks_to_smtp(self):
        """The whole point. If this regresses, a busy mail provider is a
        500 on signup again."""
        user = UserAccountFactory()

        with (
            patch("user.tasks.send_allauth_email_task.apply_async") as queued,
            patch.object(mail.EmailMultiAlternatives, "send") as direct,
        ):
            _send(user)

        assert not direct.called, (
            "the adapter opened an SMTP connection inside the request — a "
            "transient provider failure will 500 the caller again"
        )
        assert queued.called, "nothing queued; the mail would never go out"

    def test_a_busy_smtp_server_is_a_retry_in_the_task(self):
        """The exact production failure, reproduced where it now lands:
        the queued task, which retries it rather than failing a request.
        (That the request itself never talks to SMTP is the test above.)
        Eager Celery runs the task inline, so the retry surfaces here."""
        user = UserAccountFactory()

        with (
            patch.object(
                mail.EmailMultiAlternatives,
                "send",
                side_effect=smtplib.SMTPConnectError(421, b"Server busy"),
            ),
            pytest.raises(Retry),
        ):
            _send(user)

    def test_the_hand_off_carries_what_the_mail_needs(self):
        user = UserAccountFactory()

        with patch("user.tasks.send_allauth_email_task.apply_async") as queued:
            _send(user)

        kwargs = queued.call_args.kwargs["kwargs"]
        assert kwargs["type_key"] == "account.login_code"
        assert kwargs["address"] == user.email
        assert kwargs["user_pk"] == user.pk
        assert kwargs["context"] == {"code": "123456"}

    def test_only_primitives_cross_the_wire(self):
        """The project's convention for a Celery payload: no model row,
        no message, no request."""
        user = UserAccountFactory()

        with patch("user.tasks.send_allauth_email_task.apply_async") as queued:
            _send(user)

        for key, value in queued.call_args.kwargs["kwargs"].items():
            assert isinstance(value, (str, int, dict, type(None))), (
                f"{key} queued as {type(value).__name__}"
            )
        assert all(
            isinstance(text, str)
            for text in queued.call_args.kwargs["kwargs"]["context"].values()
        )

    def test_an_unmapped_prefix_is_refused(self):
        user = UserAccountFactory()
        request = RequestFactory().get("/")
        request.user = AnonymousUser()

        with request_context(request), pytest.raises(KeyError):
            UserAccountAdapter(request).send_mail(
                "account/email/not_an_email_type", user.email, {}
            )

    def test_an_account_deleted_before_the_task_runs_gets_no_mail(self):
        user = UserAccountFactory()
        kwargs = {
            "type_key": "account.login_code",
            "address": user.email,
            "language": "en",
            "user_pk": user.pk,
            "context": {"code": "123456"},
        }
        user.delete()
        mail.outbox.clear()

        result = send_allauth_email_task(**kwargs)

        assert result["status"] == "skipped"
        assert mail.outbox == []
