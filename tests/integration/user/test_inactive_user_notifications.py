from datetime import timedelta
from unittest.mock import Mock, patch

import pytest
from celery.exceptions import SoftTimeLimitExceeded
from django.core import mail
from django.test import override_settings
from django.utils import timezone

from emails import optout
from emails.gates import ScheduleGate
from emails.models import EmailOptOut, EmailProvider
from emails.transport import Receipt
from tests.utils.emails import open_email_gates  # noqa: F401
from user.factories.account import UserAccountFactory
from user.tasks import send_inactive_user_notifications

#: What the transport answers for a message it sent (a mocked ``deliver``).
SMTP_RECEIPT = Receipt(provider=EmailProvider.SMTP)


@pytest.mark.django_db
@pytest.mark.usefixtures("open_email_gates")
class TestSendInactiveUserNotificationsTask:
    @override_settings(
        NUXT_BASE_URL="https://example.com",
        DEFAULT_FROM_EMAIL="noreply@example.com",
    )
    def test_send_inactive_user_notifications_success(self):
        # Pin English locale on the inactive recipients — the mail renders
        # in ``get_user_language(user)``, and ``UserAccountFactory.
        # language_code`` defaults to ``settings.LANGUAGE_CODE`` ("el") so
        # an unpinned account would get the Greek translation and break the
        # English subject assertion below.
        UserAccountFactory(
            last_login=timezone.now() - timedelta(days=30),
            is_active=True,
            email="active@example.com",
            language_code="en",
        )
        UserAccountFactory(
            last_login=timezone.now() - timedelta(days=70),
            is_active=True,
            email="inactive@example.com",
            first_name="John",
            language_code="en",
        )
        UserAccountFactory(
            last_login=timezone.now() - timedelta(days=100),
            is_active=True,
            email="old@example.com",
            language_code="en",
        )
        UserAccountFactory(
            last_login=timezone.now() - timedelta(days=70),
            is_active=True,
            email="",
            language_code="en",
        )
        mail.outbox.clear()

        result = send_inactive_user_notifications()

        assert result["status"] == "completed"
        assert result["total_users"] == 2
        assert result["emails_sent"] == 2
        assert result["failed"] == 0

        assert len(mail.outbox) == 2
        assert {m.to[0] for m in mail.outbox} == {
            "inactive@example.com",
            "old@example.com",
        }
        assert all(m.subject == "We miss you!" for m in mail.outbox)
        assert all(
            m.from_email.endswith("<noreply@example.com>") for m in mail.outbox
        )

    @patch(
        "core.utils.email_context.tenant_logo_url",
        return_value="https://cdn.example.com/tenant-logo.svg",
    )
    def test_send_inactive_user_notifications_includes_tenant_logo(
        self, _mock_logo_url
    ):
        """``build_email_context`` (routed through by every email, this
        re-engagement send included) must surface the active tenant's logo
        as ``SITE_LOGO_URL`` in the render context."""
        UserAccountFactory(
            last_login=timezone.now() - timedelta(days=70),
            is_active=True,
            email="inactive@example.com",
            language_code="en",
        )
        mail.outbox.clear()

        result = send_inactive_user_notifications()

        assert result["emails_sent"] == 1
        [(html, _mimetype)] = mail.outbox[0].alternatives
        assert "https://cdn.example.com/tenant-logo.svg" in html

    def test_send_inactive_user_notifications_no_tenant_logo_falls_back(self):
        """Byte-parity guard: without an active tenant (this test's
        default context counts as PLATFORM), ``SITE_LOGO_URL`` resolves
        to the platform logo — only an unbranded NON-platform tenant
        gets an empty value (text wordmark)."""
        UserAccountFactory(
            last_login=timezone.now() - timedelta(days=70),
            is_active=True,
            email="inactive@example.com",
            language_code="en",
        )
        mail.outbox.clear()

        result = send_inactive_user_notifications()

        assert result["emails_sent"] == 1
        [(html, _mimetype)] = mail.outbox[0].alternatives
        assert "/static/logo-dark.svg" in html

    @patch("user.tasks.logger")
    def test_send_inactive_user_notifications_with_failures(self, mock_logger):
        UserAccountFactory(
            last_login=timezone.now() - timedelta(days=70),
            is_active=True,
            email="inactive@example.com",
            first_name="John",
        )
        UserAccountFactory(
            last_login=timezone.now() - timedelta(days=100),
            is_active=True,
            email="old@example.com",
        )

        with patch(
            "emails.send.transport.deliver",
            side_effect=[Exception("SMTP error"), SMTP_RECEIPT],
        ):
            result = send_inactive_user_notifications()

        assert result["status"] == "completed"
        assert result["total_users"] == 2
        assert result["emails_sent"] == 1
        assert result["failed"] == 1
        assert len(result["failed_details"]) == 1

    @patch("user.tasks.logger")
    def test_send_inactive_user_notifications_too_many_failures(
        self, mock_logger
    ):
        for i in range(55):
            UserAccountFactory(
                last_login=timezone.now() - timedelta(days=70),
                is_active=True,
                email=f"inactive{i}@example.com",
            )

        with patch(
            "emails.send.transport.deliver", side_effect=Exception("SMTP error")
        ):
            result = send_inactive_user_notifications()

        assert result["emails_sent"] == 0
        assert result["total_users"] == 51, "the run did not stop at the cap"
        assert len(result["failed_details"]) == 10
        mock_logger.error.assert_any_call(
            "Too many email failures, stopping task"
        )

    def test_the_soft_time_limit_stops_the_run(self):
        """Celery raises the soft limit INSIDE the task, exactly once.

        `SoftTimeLimitExceeded` is an ordinary `Exception` subclass, so
        it landed in the per-user handler, was recorded as "failed to
        send email to user X", and the loop carried on. The soft limit
        therefore bought nothing: the run continued to the hard
        `time_limit`, where Celery kills the worker process outright —
        the very outcome the soft limit exists to avoid, and one that
        also loses the summary and the in-flight user's bookkeeping.

        Stopping is a partial COMPLETION, not a failure: everyone
        already emailed carries `last_reengagement_email_at`, so the
        next scheduled run excludes them by the cooldown and continues
        from here. Re-raising would instead hand
        `autoretry_for=(Exception,)` a full rescan that can only time
        out again.
        """
        for i in range(3):
            UserAccountFactory(
                last_login=timezone.now() - timedelta(days=70),
                is_active=True,
                email=f"slow{i}@example.com",
            )

        deliver = Mock(
            side_effect=[SMTP_RECEIPT, SoftTimeLimitExceeded(), SMTP_RECEIPT]
        )
        with patch("emails.send.transport.deliver", deliver):
            result = send_inactive_user_notifications()

        assert result["timed_out"] is True
        assert result["emails_sent"] == 1
        assert result["failed"] == 0, (
            "the time limit was recorded as an email failure"
        )
        assert deliver.call_count == 2, "the loop kept going"

    def test_a_user_who_opted_out_through_the_one_click_link_is_left_out(self):
        """The one-click header records ``EmailOptOut``, not the account's
        flag, and the pipeline honours it: no mail, and the user's count and
        cooldown stay as they were."""
        user = UserAccountFactory(
            last_login=timezone.now() - timedelta(days=70),
            is_active=True,
            email="gone@example.com",
        )
        optout.opt_out(
            user.email,
            "user.reengagement",
            source=EmailOptOut.Source.ONE_CLICK,
        )
        mail.outbox.clear()

        result = send_inactive_user_notifications()

        assert result["emails_sent"] == 0
        assert mail.outbox == []
        user.refresh_from_db()
        assert user.reengagement_email_count == 0
        assert user.last_reengagement_email_at is None

    def test_a_run_that_is_not_scheduled_writes_to_no_one(self):
        UserAccountFactory(
            last_login=timezone.now() - timedelta(days=70),
            is_active=True,
            email="inactive@example.com",
        )
        mail.outbox.clear()

        with patch.object(ScheduleGate, "is_open", return_value=False):
            result = send_inactive_user_notifications()

        assert result["emails_sent"] == 0
        assert mail.outbox == []
