from unittest.mock import patch

from django.core import mail
from django.test import TestCase, override_settings

from contact.models import Feedback, FeedbackCategory
from contact.signals import send_feedback_email_notification

_INFO_EMAIL = "admin@example.com"


class TestFeedbackSignals(TestCase):
    def setUp(self):
        self.feedback_data = {
            "name": "John Doe",
            "email": "john@example.com",
            "rating": 5,
            "category": FeedbackCategory.WEBSITE,
            "message": "This checkout experience was smooth and fast.",
        }

    @override_settings(
        INFO_EMAIL=_INFO_EMAIL,
        DEFAULT_FROM_EMAIL="noreply@example.com",
    )
    def test_email_sent_on_feedback_creation(self):
        mail.outbox = []

        feedback = Feedback.objects.create(**self.feedback_data)

        assert len(mail.outbox) == 1

        email = mail.outbox[0]
        assert str(feedback.rating) in email.subject
        assert feedback.message in email.body
        assert feedback.name in email.body
        assert email.from_email.endswith("<noreply@example.com>")
        assert _INFO_EMAIL in email.to

    @override_settings(INFO_EMAIL="")
    @patch("contact.tasks.logger")
    def test_no_email_sent_when_contact_email_missing(self, mock_logger):
        mail.outbox = []

        Feedback.objects.create(**self.feedback_data)

        assert len(mail.outbox) == 0
        mock_logger.warning.assert_called_once()

    def test_no_email_sent_on_feedback_update(self):
        mail.outbox = []

        feedback = Feedback.objects.create(**self.feedback_data)
        mail.outbox = []

        feedback.rating = 3
        feedback.save()

        assert len(mail.outbox) == 0

    @override_settings(
        INFO_EMAIL=_INFO_EMAIL,
        DEFAULT_FROM_EMAIL="noreply@example.com",
    )
    def test_email_content_includes_anonymous_fallback(self):
        mail.outbox = []

        Feedback.objects.create(
            rating=2,
            category=FeedbackCategory.DELIVERY,
            message="Delivery estimate was inaccurate for my order.",
        )

        assert len(mail.outbox) == 1
        email = mail.outbox[0]
        assert "Anonymous" in email.body
        assert "—" in email.body

    def test_signal_handler_direct_call(self):
        feedback = Feedback.objects.create(**self.feedback_data)

        with (
            patch(
                "contact.tasks.send_feedback_notification_email_task.delay"
            ) as mock_delay,
            override_settings(
                INFO_EMAIL=_INFO_EMAIL,
                DEFAULT_FROM_EMAIL="noreply@example.com",
            ),
        ):
            send_feedback_email_notification(
                sender=Feedback, instance=feedback, created=True
            )

            mock_delay.assert_called_once_with(feedback.id)

    def test_signal_handler_not_created(self):
        feedback = Feedback.objects.create(**self.feedback_data)

        with patch(
            "contact.tasks.send_feedback_notification_email_task.delay"
        ) as mock_delay:
            send_feedback_email_notification(
                sender=Feedback, instance=feedback, created=False
            )

            mock_delay.assert_not_called()
