"""allauth mail must not fail the request that triggers it.

Production, 2026-09-15: smtp.gmail.com answered 421 "4.4.5 Server busy,
try again later" and the resulting ``smtplib.SMTPConnectError``
propagated out of ``/_allauth/app/v1/auth/signup`` as a 500. A real
person could not create an account because the mail provider was
briefly rate limiting us.

Rendering stays in the request — it needs the tenant, the language and
allauth's own context — but the SMTP conversation moves to Celery,
where a 421 is a retry rather than a 500.
"""

from __future__ import annotations

import smtplib
from unittest.mock import patch

import pytest
from allauth.core.context import request_context
from django.contrib.auth.models import AnonymousUser
from django.core import mail
from django.test import RequestFactory

from user.adapter import UserAccountAdapter
from user.factories.account import UserAccountFactory


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
            patch("user.tasks.send_rendered_email_task.apply_async") as queued,
            patch.object(mail.EmailMultiAlternatives, "send") as direct,
        ):
            _send(user)

        assert not direct.called, (
            "the adapter opened an SMTP connection inside the request — a "
            "transient provider failure will 500 the caller again"
        )
        assert queued.called, "nothing queued; the mail would never go out"

    def test_a_refusing_smtp_server_does_not_reach_the_caller(self):
        """The exact production failure, reproduced: the send raises,
        the caller returns normally."""
        user = UserAccountFactory()

        with patch.object(
            mail.EmailMultiAlternatives,
            "send",
            side_effect=smtplib.SMTPConnectError(421, b"Server busy"),
        ):
            _send(user)  # must not raise

    def test_subject_and_both_bodies_survive_the_hand_off(self):
        """A blank email is a worse outcome than a slow one."""
        user = UserAccountFactory()

        with patch("user.tasks.send_rendered_email_task.apply_async") as queued:
            _send(user)

        kwargs = queued.call_args.kwargs["kwargs"]
        assert kwargs["to"] == [user.email]
        assert kwargs["subject"].strip(), "subject lost in the hand-off"
        assert kwargs["body"].strip(), "text body lost in the hand-off"
        assert kwargs["html_body"], (
            "the themed HTML alternative was dropped — customers would get "
            "the unbranded plain-text fallback"
        )

    def test_only_primitives_cross_the_wire(self):
        """An EmailMessage does not belong on a Celery queue; the
        project's convention is primitives only."""
        user = UserAccountFactory()

        with patch("user.tasks.send_rendered_email_task.apply_async") as queued:
            _send(user)

        for key, value in queued.call_args.kwargs["kwargs"].items():
            assert isinstance(value, (str, list, type(None))), (
                f"{key} queued as {type(value).__name__}"
            )
