from unittest.mock import patch

import pytest
from django.core import mail
from django.test import override_settings

from tests.utils.emails import open_email_gates  # noqa: F401
from user.factories.account import UserAccountFactory
from user.models.subscription import SubscriptionTopic, UserSubscription
from user.utils.subscription import (
    send_subscription_confirmation,
)


@pytest.mark.django_db
@pytest.mark.usefixtures("open_email_gates")
class TestSubscriptionUtils:
    def setup_method(self):
        self.user = UserAccountFactory()
        self.topic = SubscriptionTopic.objects.create(
            name="Test Newsletter",
            slug="test-newsletter",
            description="Test newsletter description",
            category="news",
        )

    def _guest(self, **kwargs):
        return UserSubscription.objects.create(
            topic=self.topic,
            email="guest@example.com",
            source=UserSubscription.Source.NEWSLETTER_FORM,
            **kwargs,
        )

    @override_settings(
        SITE_NAME="Test Site",
        SUPPORT_EMAIL="support@test.com",
        DEFAULT_FROM_EMAIL="noreply@test.com",
    )
    def test_send_subscription_confirmation_success(self):
        subscription = UserSubscription.objects.create(
            user=self.user,
            topic=self.topic,
            status=UserSubscription.SubscriptionStatus.PENDING,
            confirmation_token="test-token-123",
        )
        mail.outbox.clear()

        result = send_subscription_confirmation(subscription)

        assert result is True
        [sent] = mail.outbox
        assert sent.to == [self.user.email]
        # The .txt body and the .html alternative both carry the link.
        [(html, _mimetype)] = sent.alternatives
        assert "/newsletter/confirm/test-token-123" in sent.body
        assert "/newsletter/confirm/test-token-123" in html

    @patch("user.utils.subscription.get_tenant_frontend_url")
    def test_confirmation_url_is_the_storefront_page(self, mock_frontend_url):
        """The link opens the STOREFRONT confirmation page on the tenant's
        primary domain — built like every other storefront link in mail —
        whose button POSTs the token, so a prefetching scanner confirms
        nothing."""
        mock_frontend_url.side_effect = lambda path, *, language: (
            f"https://tenant-b.example{path}"
        )
        subscription = UserSubscription.objects.create(
            user=self.user,
            topic=self.topic,
            status=UserSubscription.SubscriptionStatus.PENDING,
            confirmation_token="test-token-123",
        )
        mail.outbox.clear()

        assert send_subscription_confirmation(subscription) is True

        assert (
            "https://tenant-b.example/newsletter/confirm/test-token-123"
            in mail.outbox[0].body
        )

    def test_guest_confirmation_renders_without_a_user(self):
        """A guest row has no account: the mail goes to the row's address,
        in the row's language, and greets generically."""
        subscription = self._guest(
            status=UserSubscription.SubscriptionStatus.PENDING,
            confirmation_token="guest-token",
            language="en",
        )

        assert send_subscription_confirmation(subscription) is True

        assert len(mail.outbox) == 1
        message = mail.outbox[0]
        assert message.to == ["guest@example.com"]
        assert "/newsletter/confirm/guest-token" in message.body
        assert "Hello," in message.body
        assert "Hi " not in message.body
        html = message.alternatives[0][0]
        assert "Hello," in html

    def test_send_subscription_confirmation_not_pending(self):
        subscription = UserSubscription.objects.create(
            user=self.user,
            topic=self.topic,
            status=UserSubscription.SubscriptionStatus.ACTIVE,
            confirmation_token="test-token-123",
        )

        result = send_subscription_confirmation(subscription)

        assert result is False

    def test_send_subscription_confirmation_no_token(self):
        subscription = UserSubscription.objects.create(
            user=self.user,
            topic=self.topic,
            status=UserSubscription.SubscriptionStatus.PENDING,
            confirmation_token="",
        )

        result = send_subscription_confirmation(subscription)

        assert result is False

    @override_settings(
        DEFAULT_FROM_EMAIL="noreply@test.com",
    )
    def test_send_subscription_confirmation_email_exception(self):
        subscription = UserSubscription.objects.create(
            user=self.user,
            topic=self.topic,
            status=UserSubscription.SubscriptionStatus.PENDING,
            confirmation_token="test-token-123",
        )

        # Raised, not swallowed: the task's retry policy depends on it.
        with (
            patch(
                "emails.send.transport.deliver",
                side_effect=Exception("Email sending failed"),
            ),
            pytest.raises(Exception, match="Email sending failed"),
        ):
            send_subscription_confirmation(subscription)

    @patch("user.utils.subscription.logger")
    def test_send_subscription_confirmation_logs_warning_not_pending(
        self, mock_logger
    ):
        subscription = UserSubscription.objects.create(
            user=self.user,
            topic=self.topic,
            status=UserSubscription.SubscriptionStatus.ACTIVE,
            confirmation_token="test-token",
        )

        send_subscription_confirmation(subscription)

        mock_logger.warning.assert_called_once()
        assert "non-pending" in mock_logger.warning.call_args[0][0]

    @patch("user.utils.subscription.logger")
    def test_send_subscription_confirmation_logs_error_no_token(
        self, mock_logger
    ):
        subscription = UserSubscription.objects.create(
            user=self.user,
            topic=self.topic,
            status=UserSubscription.SubscriptionStatus.PENDING,
            confirmation_token="",
        )

        send_subscription_confirmation(subscription)

        mock_logger.error.assert_called_once()
        assert "No confirmation token" in mock_logger.error.call_args[0][0]
