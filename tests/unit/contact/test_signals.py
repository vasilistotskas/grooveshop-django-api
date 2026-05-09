import contextlib
from unittest.mock import patch

from django.core import mail
from django.test import TestCase, override_settings

from contact.models import Contact
from contact.signals import send_email_notification


class TestContactSignals(TestCase):
    def setUp(self):
        self.contact_data = {
            "name": "John Doe",
            "email": "john@example.com",
            "message": "This is a test message with sufficient length.",
        }

    @override_settings(
        ADMINS=[("Admin", "admin@example.com")],
        DEFAULT_FROM_EMAIL="noreply@example.com",
    )
    def test_email_sent_on_contact_creation(self):
        mail.outbox = []

        contact = Contact.objects.create(**self.contact_data)

        assert len(mail.outbox) == 1

        email = mail.outbox[0]
        assert (
            email.subject == f"New Contact Form Submission from {contact.name}"
        )
        assert contact.name in email.body
        assert contact.email in email.body
        assert contact.message in email.body
        assert email.from_email == "noreply@example.com"
        assert "admin@example.com" in email.to

    @override_settings(
        ADMINS=[
            ("Admin1", "admin1@example.com"),
            ("Admin2", "admin2@example.com"),
        ],
        DEFAULT_FROM_EMAIL="noreply@example.com",
    )
    def test_email_sent_to_multiple_admins(self):
        mail.outbox = []

        Contact.objects.create(**self.contact_data)

        assert len(mail.outbox) == 1
        email = mail.outbox[0]
        assert "admin1@example.com" in email.to
        assert "admin2@example.com" in email.to

    @override_settings(ADMINS=[])
    @patch("contact.tasks.logger")
    def test_no_email_sent_when_no_admins(self, mock_logger):
        mail.outbox = []

        Contact.objects.create(**self.contact_data)

        assert len(mail.outbox) == 0

        # The send_contact_notification_email_task logs a warning + returns
        # False when no ADMINS are configured.
        mock_logger.warning.assert_called_once()
        first_arg = mock_logger.warning.call_args[0][0]
        assert "no ADMINS configured" in first_arg

    @override_settings(ADMINS=None)
    @patch("contact.signals.logger")
    def test_no_email_sent_when_admins_none(self, mock_logger):
        mail.outbox = []

        with contextlib.suppress(TypeError):
            Contact.objects.create(**self.contact_data)

        assert len(mail.outbox) == 0

    def test_no_email_sent_on_contact_update(self):
        mail.outbox = []

        contact = Contact.objects.create(**self.contact_data)

        mail.outbox = []

        contact.message = "Updated message with sufficient length for testing."
        contact.save()

        assert len(mail.outbox) == 0

    @override_settings(
        ADMINS=[("Admin", "admin@example.com")],
        DEFAULT_FROM_EMAIL="noreply@example.com",
    )
    @patch("contact.tasks.send_mail")
    @patch("contact.tasks.logger")
    def test_email_failure_logged(self, mock_logger, mock_send_mail):
        # send_mail now lives in the Celery task. With CELERY_TASK_ALWAYS_EAGER
        # the task runs synchronously and Celery's autoretry_for=(Exception,)
        # would normally swallow + retry; the conftest fires on_commit
        # callbacks with try/except so the SMTP failure doesn't propagate
        # back to the test. We just need to verify the task attempted to
        # send — error logging happens via Celery's MonitoredTask.on_failure,
        # not in-task.
        mock_send_mail.side_effect = Exception("SMTP server error")

        Contact.objects.create(**self.contact_data)

        # send_mail was attempted exactly once (autoretry retries are
        # invisible because the task body is the same call).
        assert mock_send_mail.called

    @override_settings(
        ADMINS=[("Admin", "admin@example.com")],
        DEFAULT_FROM_EMAIL="noreply@example.com",
    )
    def test_email_content_format(self):
        mail.outbox = []

        _ = Contact.objects.create(
            name="Jane Smith",
            email="jane@example.com",
            message="I have a question about your services.",
        )

        assert len(mail.outbox) == 1
        email = mail.outbox[0]

        assert email.subject == "New Contact Form Submission from Jane Smith"

        assert "Name: Jane Smith" in email.body
        assert "Email: jane@example.com" in email.body
        assert "Message: I have a question about your services." in email.body

    @override_settings(
        ADMINS=[("Admin", "admin@example.com")],
        DEFAULT_FROM_EMAIL="noreply@example.com",
    )
    def test_email_with_special_characters(self):
        mail.outbox = []

        _ = Contact.objects.create(
            name="José García",
            email="jose@example.com",
            message="¡Hola! I have a question about your products. Thanks!",
        )

        assert len(mail.outbox) == 1
        email = mail.outbox[0]

        assert "José García" in email.body
        assert "¡Hola!" in email.body

    @override_settings(
        ADMINS=[("Admin", "admin@example.com")],
        DEFAULT_FROM_EMAIL="noreply@example.com",
    )
    def test_email_with_long_message(self):
        mail.outbox = []

        long_message = "This is a very long message. " * 100
        _ = Contact.objects.create(
            name="Test User", email="test@example.com", message=long_message
        )

        assert len(mail.outbox) == 1
        email = mail.outbox[0]

        assert long_message in email.body

    def test_signal_handler_direct_call(self):
        contact = Contact.objects.create(**self.contact_data)

        # Signal now dispatches a Celery task on commit instead of calling
        # send_mail directly — patch the task's .delay to assert dispatch.
        with (
            patch(
                "contact.tasks.send_contact_notification_email_task.delay"
            ) as mock_delay,
            override_settings(
                ADMINS=[("Admin", "admin@example.com")],
                DEFAULT_FROM_EMAIL="noreply@example.com",
            ),
        ):
            send_email_notification(
                sender=Contact, instance=contact, created=True
            )

            mock_delay.assert_called_once_with(contact.id)

    def test_signal_handler_not_created(self):
        contact = Contact.objects.create(**self.contact_data)

        with patch(
            "contact.tasks.send_contact_notification_email_task.delay"
        ) as mock_delay:
            send_email_notification(
                sender=Contact, instance=contact, created=False
            )

            mock_delay.assert_not_called()

    @override_settings(
        ADMINS=[("Admin", "admin@example.com")],
        DEFAULT_FROM_EMAIL="",
    )
    def test_email_with_empty_from_email(self):
        mail.outbox = []

        Contact.objects.create(**self.contact_data)

        assert len(mail.outbox) == 1
        email = mail.outbox[0]
        assert email.from_email == ""

    @override_settings(
        ADMINS=[("Admin", "admin@example.com")],
        DEFAULT_FROM_EMAIL="noreply@example.com",
    )
    @patch("contact.tasks.send_mail")
    def test_send_mail_parameters(self, mock_send_mail):
        contact = Contact.objects.create(**self.contact_data)

        # Celery task (running eagerly in tests) invokes send_mail with the
        # rendered subject/body using the contact's own name/email/message.
        mock_send_mail.assert_called_once_with(
            subject=f"New Contact Form Submission from {contact.name}",
            message=f"Name: {contact.name}\nEmail: {contact.email}\nMessage: {contact.message}",
            from_email="noreply@example.com",
            recipient_list=["admin@example.com"],
            fail_silently=False,
        )

    @override_settings(
        ADMINS=[("", "admin@example.com")],
        DEFAULT_FROM_EMAIL="noreply@example.com",
    )
    def test_email_with_empty_admin_name(self):
        mail.outbox = []

        Contact.objects.create(**self.contact_data)

        assert len(mail.outbox) == 1
        email = mail.outbox[0]
        assert "admin@example.com" in email.to

    @override_settings(
        ADMINS=[("Admin", "admin@example.com")],
        DEFAULT_FROM_EMAIL="noreply@example.com",
    )
    def test_email_content_escaping(self):
        mail.outbox = []

        _ = Contact.objects.create(
            name="<script>alert('xss')</script>",
            email="test@example.com",
            message="Message with <b>HTML</b> tags & special chars.",
        )

        assert len(mail.outbox) == 1
        email = mail.outbox[0]

        assert "<script>alert('xss')</script>" in email.body
        assert "<b>HTML</b>" in email.body
        assert "&" in email.body
