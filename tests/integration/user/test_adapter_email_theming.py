"""Multipart HTML rendering for django-allauth account emails.

``UserAccountAdapter.send_mail`` merges in the project's shared email
theme context (``core.utils.email_context.build_email_context``)
before delegating to allauth's own ``render_mail``, so
``core/templates/account/email/*_message.html`` templates (added
alongside allauth's own ``.txt`` originals) now render and attach as
the ``text/html`` alternative on the outbound multipart message —
previously ``core/templates/account/`` didn't exist at all, so every
allauth account email (login code, password reset, email
confirmation, ...) went out as unbranded plain text.
"""

from __future__ import annotations

import pytest
from allauth.core.context import request_context
from django.contrib.auth.models import AnonymousUser
from django.core import mail
from django.test import RequestFactory

from user.adapter import UserAccountAdapter
from user.factories.account import UserAccountFactory


@pytest.mark.django_db
class TestAccountEmailMultipartRendering:
    def test_login_code_email_attaches_themed_html_alternative(self):
        user = UserAccountFactory()
        request = RequestFactory().get("/")
        # Templates render through core's context processors (metadata),
        # which read request.user — RequestFactory skips
        # AuthenticationMiddleware, so it's never set by default.
        request.user = AnonymousUser()

        mail.outbox = []
        with request_context(request):
            UserAccountAdapter(request).send_mail(
                "account/email/login_code",
                user.email,
                {"user": user, "code": "123456"},
            )

        assert len(mail.outbox) == 1
        msg = mail.outbox[0]

        assert len(msg.alternatives) == 1, (
            "expected exactly one text/html alternative attached"
        )
        html_body, mimetype = msg.alternatives[0]
        assert mimetype == "text/html"
        assert "123456" in html_body
        # Rendered through the shared base — not a bare unbranded body.
        assert "email-wrapper" in html_body
