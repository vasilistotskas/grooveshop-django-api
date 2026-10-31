"""A confirmation the mail server refuses for good marks the row BOUNCED.

``BOUNCED`` was a status nothing ever set, while the admin guide promised
it for an undeliverable address. The one bounce this platform can see is
the synchronous one: the SMTP server refusing every recipient with a 5xx.
"""

from __future__ import annotations

import smtplib
from unittest.mock import patch

import pytest
from django.utils import timezone

from user.models.subscription import SubscriptionTopic, UserSubscription
from user.tasks import send_subscription_confirmation_email_task

pytestmark = pytest.mark.django_db

Status = UserSubscription.SubscriptionStatus
SEND = "user.utils.subscription.send_subscription_confirmation"


@pytest.fixture
def row():
    topic = SubscriptionTopic.objects.create(
        slug="newsletter",
        name="Newsletter",
        category=SubscriptionTopic.TopicCategory.NEWSLETTER,
        is_default=True,
    )
    row = UserSubscription(topic=topic, email="gone@example.com")
    row.arm_confirmation()
    row.save()
    return row


def _refused(code):
    return smtplib.SMTPRecipientsRefused(
        {"gone@example.com": (code, b"5.1.1 user unknown")}
    )


class TestPermanentRefusal:
    def test_marks_the_row_bounced_and_does_not_retry(self, row):
        with (
            patch(SEND, side_effect=_refused(550)),
            patch.object(
                send_subscription_confirmation_email_task, "retry"
            ) as retry,
        ):
            sent = send_subscription_confirmation_email_task.run(row.id)

        assert sent is False
        retry.assert_not_called()
        row.refresh_from_db()
        assert row.status == Status.BOUNCED

    def test_a_temporary_refusal_is_retried_and_left_pending(self, row):
        with (
            patch(SEND, side_effect=_refused(450)),
            patch.object(
                send_subscription_confirmation_email_task,
                "retry",
                side_effect=RuntimeError("retry scheduled"),
            ) as retry,
            pytest.raises(RuntimeError, match="retry scheduled"),
        ):
            send_subscription_confirmation_email_task.run(row.id)

        retry.assert_called_once()
        row.refresh_from_db()
        assert row.status == Status.PENDING

    def test_a_server_outage_is_not_a_bounce(self, row):
        with (
            patch(SEND, side_effect=OSError("smtp down")),
            patch.object(
                send_subscription_confirmation_email_task,
                "retry",
                side_effect=RuntimeError("retry scheduled"),
            ),
            pytest.raises(RuntimeError),
        ):
            send_subscription_confirmation_email_task.run(row.id)

        row.refresh_from_db()
        assert row.status == Status.PENDING


class TestResubscribingAfterABounce:
    def test_a_new_request_starts_over_with_a_fresh_link(self, row):
        from user.services.subscription import (
            NewsletterFormOutcome,
            subscribe_email_to_newsletter,
        )

        row.mark_bounced()

        result = subscribe_email_to_newsletter(
            topic=row.topic,
            email=row.email,
            consent_text="I agree.",
            consent_ip=None,
            consent_user_agent="",
            language="en",
            consented_at=timezone.now(),
        )

        assert result is not None
        assert result.outcome is NewsletterFormOutcome.ARMED
        row.refresh_from_db()
        assert row.status == Status.PENDING


class TestAlreadySubscribedNoticeRefusal:
    def _active(self, row):
        UserSubscription.objects.filter(pk=row.pk).update(status=Status.ACTIVE)
        row.refresh_from_db()
        return row

    def test_a_permanent_refusal_marks_the_active_row_bounced(self, row):
        from user.tasks import send_newsletter_already_subscribed_email_task

        row = self._active(row)
        with patch(
            "user.utils.subscription.send_already_subscribed_notice",
            side_effect=_refused(550),
        ):
            sent = send_newsletter_already_subscribed_email_task.run(row.id)

        assert sent is False
        row.refresh_from_db()
        assert row.status == Status.BOUNCED

    def test_a_temporary_refusal_is_raised_and_leaves_the_row_active(self, row):
        from user.tasks import send_newsletter_already_subscribed_email_task

        row = self._active(row)
        with (
            patch(
                "user.utils.subscription.send_already_subscribed_notice",
                side_effect=_refused(450),
            ),
            pytest.raises(smtplib.SMTPRecipientsRefused),
        ):
            send_newsletter_already_subscribed_email_task.run(row.id)

        row.refresh_from_db()
        assert row.status == Status.ACTIVE
