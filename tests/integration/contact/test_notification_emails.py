"""The store's copies of contact and feedback submissions.

They were bare ``send_mail`` calls: an English f-string subject whatever
language the store speaks, a plain-text body built by hand, and no
``Reply-To``, so the store pressed reply and answered its own inbox
instead of the customer. They are now rendered from a template pair in the
store's language, sent through the same sender path as every other
store-facing email, and carry the submitter's address as ``Reply-To``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.core import mail

from contact.factories import ContactFactory, FeedbackFactory
from contact.tasks import (
    send_contact_notification_email_task,
    send_feedback_notification_email_task,
)

pytestmark = pytest.mark.django_db

STORE_INBOX = "owner@store.test"


@pytest.fixture
def store(bind_tenant, settings):
    settings.DEFAULT_FROM_EMAIL = "noreply@platform.test"

    def _bind(locale: str = "en") -> None:
        bind_tenant(
            SimpleNamespace(
                schema_name="acme",
                default_locale=locale,
                store_name="Acme Gadgets",
                contact_email=STORE_INBOX,
            )
        )
        mail.outbox.clear()

    return _bind


class TestContactNotification:
    def test_reply_goes_to_the_submitter(self, store):
        store()
        contact = ContactFactory(email="customer@example.com")

        assert send_contact_notification_email_task.run(contact.pk) is True

        [sent] = mail.outbox
        assert sent.reply_to == ["customer@example.com"]
        assert sent.to == [STORE_INBOX]

    @pytest.mark.parametrize(
        "forged",
        [
            "customer@example.com\r\nBcc: victim@example.com",
            "Mallory <mallory@example.com>",
            "a@example.com, b@example.com",
            "not an address",
        ],
    )
    def test_an_address_that_is_not_one_bare_address_never_reaches_a_header(
        self, store, forged
    ):
        store()
        contact = ContactFactory(email=forged)

        assert send_contact_notification_email_task.run(contact.pk) is True

        [sent] = mail.outbox
        assert sent.reply_to == []
        headers = sent.message()
        assert headers["Reply-To"] is None
        assert headers["Bcc"] is None
        assert sent.recipients() == [STORE_INBOX]

    @pytest.mark.parametrize(
        ("locale", "subject"),
        [
            ("en", "New contact form submission from Jane"),
            ("el", "Νέο μήνυμα από τη φόρμα επικοινωνίας: Jane"),
        ],
    )
    def test_subject_is_in_the_stores_language(self, store, locale, subject):
        store(locale)
        contact = ContactFactory(name="Jane")

        send_contact_notification_email_task.run(contact.pk)

        assert mail.outbox[-1].subject == subject

    def test_sender_is_the_stores_own_sender_path(self, store):
        store()
        contact = ContactFactory()

        send_contact_notification_email_task.run(contact.pk)

        assert mail.outbox[-1].from_email == (
            "Acme Gadgets <noreply@platform.test>"
        )

    def test_it_is_a_branded_multipart_email(self, store):
        store()
        contact = ContactFactory(
            name="Jane", message="Line one\nLine two", phone="2101234567"
        )

        send_contact_notification_email_task.run(contact.pk)

        [sent] = mail.outbox
        html, mimetype = sent.alternatives[0]
        assert mimetype == "text/html"
        assert "email-wrapper" in html
        assert "Line one<br>Line two" in html
        assert "Name: Jane" in sent.body
        assert "Phone: 2101234567" in sent.body
        assert "Message: Line one\nLine two" in sent.body


class TestFeedbackNotification:
    def test_reply_goes_to_the_submitter_when_they_left_an_address(self, store):
        store()
        feedback = FeedbackFactory(email="fan@example.com")

        assert send_feedback_notification_email_task.run(feedback.pk) is True

        assert mail.outbox[-1].reply_to == ["fan@example.com"]

    def test_an_anonymous_submission_has_no_reply_to(self, store):
        store()
        feedback = FeedbackFactory(email="", name="")

        send_feedback_notification_email_task.run(feedback.pk)

        [sent] = mail.outbox
        assert sent.reply_to == []
        assert "Anonymous" in sent.body

    def test_a_forged_address_never_reaches_a_header(self, store):
        store()
        feedback = FeedbackFactory(
            email="fan@example.com\nBcc: victim@example.com"
        )

        send_feedback_notification_email_task.run(feedback.pk)

        [sent] = mail.outbox
        assert sent.reply_to == []
        headers = sent.message()
        assert headers["Reply-To"] is None
        assert headers["Bcc"] is None
        assert sent.recipients() == [STORE_INBOX]

    @pytest.mark.parametrize(
        ("locale", "subject"),
        [
            ("en", "New feedback (4★ Website & UX) submission"),
            ("el", "Νέα υποβολή σχολίων (4★ Ιστότοπος & UX)"),
        ],
    )
    def test_subject_is_in_the_stores_language(self, store, locale, subject):
        store(locale)
        feedback = FeedbackFactory(rating=4, category="website")

        send_feedback_notification_email_task.run(feedback.pk)

        assert mail.outbox[-1].subject == subject

    def test_sender_is_the_stores_own_sender_path(self, store):
        store()
        feedback = FeedbackFactory()

        send_feedback_notification_email_task.run(feedback.pk)

        assert mail.outbox[-1].from_email == (
            "Acme Gadgets <noreply@platform.test>"
        )
