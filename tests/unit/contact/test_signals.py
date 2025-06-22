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
    @patch("contact.signals.logger")
    def test_no_email_sent_when_no_admins(self, mock_logger):
        mail.outbox = []

        Contact.objects.create(**self.contact_data)

        assert len(mail.outbox) == 0

        mock_logger.warning.assert_called_once_with(
            "No admin recipient found in settings.ADMINS"
        )

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
    @patch("contact.signals.send_mail")
    @patch("contact.signals.logger")
    def test_email_failure_logged(self, mock_logger, mock_send_mail):
        mock_send_mail.side_effect = Exception("SMTP server error")

        Contact.objects.create(**self.contact_data)

        mock_logger.error.assert_called_once()
        error_call_args = mock_logger.error.call_args[0][0]
        assert "Failed to send email" in error_call_args
        assert "SMTP server error" in error_call_args

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

        with (
            patch("contact.signals.send_mail") as mock_send_mail,
            override_settings(
                ADMINS=[("Admin", "admin@example.com")],
                DEFAULT_FROM_EMAIL="noreply@example.com",
            ),
        ):
            send_email_notification(
                sender=Contact, instance=contact, created=True
            )

            mock_send_mail.assert_called_once()
            call_args = mock_send_mail.call_args
            assert (
                call_args[1]["subject"]
                == f"New Contact Form Submission from {contact.name}"
            )
            assert call_args[1]["from_email"] == "noreply@example.com"
            assert call_args[1]["recipient_list"] == ["admin@example.com"]

    def test_signal_handler_not_created(self):
        contact = Contact.objects.create(**self.contact_data)

        with patch("contact.signals.send_mail") as mock_send_mail:
            send_email_notification(
                sender=Contact, instance=contact, created=False
            )

            mock_send_mail.assert_not_called()

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
    @patch("contact.signals.send_mail")
    def test_send_mail_parameters(self, mock_send_mail):
        contact = Contact.objects.create(**self.contact_data)

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
