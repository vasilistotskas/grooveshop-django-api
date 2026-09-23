from unittest.mock import MagicMock, patch

import pytest
from django.core import mail, signing
from django.test import override_settings

from user.factories.account import UserAccountFactory
from user.models.subscription import SubscriptionTopic, UserSubscription
from user.utils.subscription import (
    UNSUBSCRIBE_SALT,
    generate_unsubscribe_link,
    send_subscription_confirmation,
)


@pytest.mark.django_db
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
    @patch("user.utils.subscription.render_to_string")
    @patch("user.utils.subscription.EmailMultiAlternatives")
    def test_send_subscription_confirmation_success(
        self, mock_email_class, mock_render
    ):
        mock_render.return_value = "<html>Test confirmation email</html>"
        mock_email = MagicMock()
        mock_email_class.return_value = mock_email

        subscription = UserSubscription.objects.create(
            user=self.user,
            topic=self.topic,
            status=UserSubscription.SubscriptionStatus.PENDING,
            confirmation_token="test-token-123",
        )

        result = send_subscription_confirmation(subscription)

        assert result is True
        # Renders both the .html body and the dedicated .txt body.
        assert mock_render.call_count == 2
        rendered_templates = {
            call.args[0] for call in mock_render.call_args_list
        }
        assert rendered_templates == {
            "emails/subscription/confirmation.html",
            "emails/subscription/confirmation.txt",
        }
        assert mock_email_class.call_args.kwargs["to"] == [self.user.email]
        mock_email.attach_alternative.assert_called_once()
        mock_email.send.assert_called_once()

    @patch("user.utils.subscription.render_to_string")
    @patch("user.utils.subscription.EmailMultiAlternatives")
    @patch("user.utils.subscription.get_tenant_frontend_url")
    def test_confirmation_url_is_the_storefront_page(
        self, mock_frontend_url, mock_email_class, mock_render
    ):
        """The link opens the STOREFRONT confirmation page on the tenant's
        primary domain — built like every other storefront link in mail —
        whose button POSTs the token, so a prefetching scanner confirms
        nothing."""
        mock_frontend_url.side_effect = lambda path: (
            f"https://tenant-b.example{path}"
        )
        mock_render.return_value = "<html></html>"
        mock_email_class.return_value = MagicMock()

        subscription = UserSubscription.objects.create(
            user=self.user,
            topic=self.topic,
            status=UserSubscription.SubscriptionStatus.PENDING,
            confirmation_token="test-token-123",
        )

        assert send_subscription_confirmation(subscription) is True
        rendered_context = mock_render.call_args_list[0].args[1]
        assert rendered_context["confirmation_url"] == (
            "https://tenant-b.example/newsletter/confirm/test-token-123"
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
    @patch("user.utils.subscription.render_to_string")
    @patch("user.utils.subscription.EmailMultiAlternatives")
    def test_send_subscription_confirmation_email_exception(
        self, mock_email_class, mock_render
    ):
        mock_render.return_value = "<html>Test email</html>"
        mock_email = MagicMock()
        mock_email.send.side_effect = Exception("Email sending failed")
        mock_email_class.return_value = mock_email

        subscription = UserSubscription.objects.create(
            user=self.user,
            topic=self.topic,
            status=UserSubscription.SubscriptionStatus.PENDING,
            confirmation_token="test-token-123",
        )

        # Raised, not swallowed: the task's retry policy depends on it.
        with pytest.raises(Exception, match="Email sending failed"):
            send_subscription_confirmation(subscription)

    @patch("user.utils.subscription._make_unsubscribe_token")
    @patch("user.utils.subscription.get_tenant_api_base_url")
    def test_generate_unsubscribe_link_uses_tenant_api_base_url(
        self, mock_api_base, mock_token
    ):
        """The unsubscribe URL must use the TENANT's API host, not the
        platform-wide API_BASE_URL — the token bakes in the tenant's
        schema and a platform-host link is rejected on every
        non-platform tenant (RFC 8058 one-click unsubscribe break)."""
        mock_token.return_value = "signed-token"
        mock_api_base.return_value = "https://api.tenant-b.example"
        subscription = UserSubscription.objects.create(
            user=self.user, topic=self.topic
        )

        result = generate_unsubscribe_link(subscription)

        expected_url = (
            "https://api.tenant-b.example/api/v1/user/unsubscribe/"
            "signed-token/test-newsletter"
        )
        assert result == expected_url
        mock_api_base.assert_called_once()

    @override_settings(API_BASE_URL="https://api.test-site.com")
    @patch("user.utils.subscription._make_unsubscribe_token")
    def test_generate_unsubscribe_link(self, mock_token):
        mock_token.return_value = "signed-token"
        subscription = UserSubscription.objects.create(
            user=self.user, topic=self.topic
        )

        result = generate_unsubscribe_link(subscription)

        # Points directly at the Django API so List-Unsubscribe headers work
        # without a Nuxt frontend page. The signed token is a single path
        # segment (no uidb64 — the token already carries the user pk).
        expected_url = "https://api.test-site.com/api/v1/user/unsubscribe/signed-token/test-newsletter"
        assert result == expected_url
        mock_token.assert_called_once_with(self.user)

    @override_settings(API_BASE_URL="https://api.test-site.com/")
    @patch("user.utils.subscription._make_unsubscribe_token")
    def test_generate_unsubscribe_link_strips_trailing_slash(self, mock_token):
        """Trailing slash on API_BASE_URL must not produce a double slash."""
        mock_token.return_value = "signed-token"
        subscription = UserSubscription.objects.create(
            user=self.user, topic=self.topic
        )

        result = generate_unsubscribe_link(subscription)

        expected_url = "https://api.test-site.com/api/v1/user/unsubscribe/signed-token/test-newsletter"
        assert result == expected_url

    @override_settings(API_BASE_URL="https://api.test-site.com")
    def test_unsubscribe_token_round_trips_to_user_pk(self):
        """The generated token is a signing value that decodes back to the
        user's pk under the dedicated salt — independent of the user's
        password/last_login (unlike the old password-reset token)."""
        subscription = UserSubscription.objects.create(
            user=self.user, topic=self.topic
        )
        url = generate_unsubscribe_link(subscription)
        token = url.rsplit("/", 2)[1]

        assert signing.loads(token, salt=UNSUBSCRIBE_SALT) == {
            "schema": "public",
            "pk": self.user.pk,
        }

    @override_settings(API_BASE_URL="https://api.test-site.com")
    def test_guest_unsubscribe_token_names_the_subscription(self):
        """A guest has no account pk: the token signs the row's uuid
        (``sid``) and the owning schema."""
        subscription = self._guest()
        url = generate_unsubscribe_link(subscription)
        token = url.rsplit("/", 2)[1]

        assert signing.loads(token, salt=UNSUBSCRIBE_SALT) == {
            "schema": "public",
            "sid": str(subscription.uuid),
        }

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
