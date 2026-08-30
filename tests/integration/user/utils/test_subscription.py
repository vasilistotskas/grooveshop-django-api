from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings

from user.factories.account import UserAccountFactory
from user.models.subscription import SubscriptionTopic, UserSubscription
from user.utils.subscription import (
    check_subscription_before_send,
    generate_unsubscribe_link,
    get_user_subscription_summary,
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

    def test_check_subscription_before_send_active_exists(self):
        UserSubscription.objects.create(
            user=self.user,
            topic=self.topic,
            status=UserSubscription.SubscriptionStatus.ACTIVE,
        )

        result = check_subscription_before_send(self.user, self.topic.slug)

        assert result is True

    def test_check_subscription_before_send_no_active(self):
        UserSubscription.objects.create(
            user=self.user,
            topic=self.topic,
            status=UserSubscription.SubscriptionStatus.PENDING,
        )

        result = check_subscription_before_send(self.user, self.topic.slug)

        assert result is False

    def test_check_subscription_before_send_no_subscription(self):
        result = check_subscription_before_send(self.user, self.topic.slug)

        assert result is False

    @override_settings(
        SITE_NAME="Test Site",
        SUPPORT_EMAIL="support@test.com",
        DEFAULT_FROM_EMAIL="noreply@test.com",
    )
    @patch("user.utils.subscription.render_to_string")
    @patch("user.utils.subscription.EmailMultiAlternatives")
    @patch("user.utils.subscription.check_subscription_before_send")
    def test_send_subscription_confirmation_success(
        self, mock_check, mock_email_class, mock_render
    ):
        mock_check.return_value = False
        mock_render.return_value = "<html>Test confirmation email</html>"
        mock_email = MagicMock()
        mock_email_class.return_value = mock_email

        subscription = UserSubscription.objects.create(
            user=self.user,
            topic=self.topic,
            status=UserSubscription.SubscriptionStatus.PENDING,
            confirmation_token="test-token-123",
        )

        result = send_subscription_confirmation(subscription, self.user)

        assert result is True
        mock_check.assert_called_once_with(
            user=self.user, topic_slug=self.topic.slug
        )
        # Renders both the .html body and the dedicated .txt body.
        assert mock_render.call_count == 2
        rendered_templates = {
            call.args[0] for call in mock_render.call_args_list
        }
        assert rendered_templates == {
            "emails/subscription/confirmation.html",
            "emails/subscription/confirmation.txt",
        }
        mock_email_class.assert_called_once()
        mock_email.attach_alternative.assert_called_once()
        mock_email.send.assert_called_once()

    @patch("user.utils.subscription.render_to_string")
    @patch("user.utils.subscription.EmailMultiAlternatives")
    @patch("user.utils.subscription.check_subscription_before_send")
    @patch("user.utils.subscription.get_tenant_api_base_url")
    def test_send_subscription_confirmation_uses_tenant_api_base_url(
        self, mock_api_base, mock_check, mock_email_class, mock_render
    ):
        """confirmation_url must be built against the tenant's API host,
        not the platform-wide API_BASE_URL — there is no Nuxt proxy for
        this Django-only endpoint."""
        mock_api_base.return_value = "https://api.tenant-b.example"
        mock_check.return_value = False
        mock_render.return_value = "<html>Test confirmation email</html>"
        mock_email_class.return_value = MagicMock()

        subscription = UserSubscription.objects.create(
            user=self.user,
            topic=self.topic,
            status=UserSubscription.SubscriptionStatus.PENDING,
            confirmation_token="test-token-123",
        )

        result = send_subscription_confirmation(subscription, self.user)

        assert result is True
        mock_api_base.assert_called_once()
        rendered_context = mock_render.call_args_list[0].args[1]
        assert rendered_context["confirmation_url"] == (
            "https://api.tenant-b.example"
            "/api/v1/user/subscription/confirm/test-token-123"
        )

    @patch("user.utils.subscription.check_subscription_before_send")
    def test_send_subscription_confirmation_already_active(self, mock_check):
        mock_check.return_value = True

        subscription = UserSubscription.objects.create(
            user=self.user,
            topic=self.topic,
            status=UserSubscription.SubscriptionStatus.PENDING,
            confirmation_token="test-token-123",
        )

        result = send_subscription_confirmation(subscription, self.user)

        assert result is False

    def test_send_subscription_confirmation_not_pending(self):
        subscription = UserSubscription.objects.create(
            user=self.user,
            topic=self.topic,
            status=UserSubscription.SubscriptionStatus.ACTIVE,
            confirmation_token="test-token-123",
        )

        result = send_subscription_confirmation(subscription, self.user)

        assert result is False

    def test_send_subscription_confirmation_no_token(self):
        subscription = UserSubscription.objects.create(
            user=self.user,
            topic=self.topic,
            status=UserSubscription.SubscriptionStatus.PENDING,
            confirmation_token="",
        )

        result = send_subscription_confirmation(subscription, self.user)

        assert result is False

    @override_settings(
        DEFAULT_FROM_EMAIL="noreply@test.com",
    )
    @patch("user.utils.subscription.render_to_string")
    @patch("user.utils.subscription.EmailMultiAlternatives")
    @patch("user.utils.subscription.check_subscription_before_send")
    def test_send_subscription_confirmation_email_exception(
        self, mock_check, mock_email_class, mock_render
    ):
        mock_check.return_value = False
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

        result = send_subscription_confirmation(subscription, self.user)

        assert result is False

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

        result = generate_unsubscribe_link(self.user, self.topic)

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

        result = generate_unsubscribe_link(self.user, self.topic)

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

        result = generate_unsubscribe_link(self.user, self.topic)

        expected_url = "https://api.test-site.com/api/v1/user/unsubscribe/signed-token/test-newsletter"
        assert result == expected_url

    @override_settings(API_BASE_URL="https://api.test-site.com")
    def test_unsubscribe_token_round_trips_to_user_pk(self):
        """The generated token is a signing value that decodes back to the
        user's pk under the dedicated salt — independent of the user's
        password/last_login (unlike the old password-reset token)."""
        from django.core import signing

        from user.utils.subscription import UNSUBSCRIBE_SALT

        url = generate_unsubscribe_link(self.user, self.topic)
        token = url.rsplit("/", 2)[1]

        assert signing.loads(token, salt=UNSUBSCRIBE_SALT) == {
            "schema": "public",
            "pk": self.user.pk,
        }

    def test_get_user_subscription_summary_empty(self):
        result = get_user_subscription_summary(self.user)

        expected = {
            "total": 0,
            "active": 0,
            "pending": 0,
            "unsubscribed": 0,
            "by_category": {},
        }

        assert result == expected

    def test_get_user_subscription_summary_with_subscriptions(self):
        topic2 = SubscriptionTopic.objects.create(
            name="Tech News",
            slug="tech-news",
            description="Technology news",
            category="tech",
        )

        topic3 = SubscriptionTopic.objects.create(
            name="Sports Update",
            slug="sports-update",
            description="Sports updates",
            category="news",
        )

        UserSubscription.objects.create(
            user=self.user,
            topic=self.topic,
            status=UserSubscription.SubscriptionStatus.ACTIVE,
        )

        UserSubscription.objects.create(
            user=self.user,
            topic=topic2,
            status=UserSubscription.SubscriptionStatus.ACTIVE,
        )

        UserSubscription.objects.create(
            user=self.user,
            topic=topic3,
            status=UserSubscription.SubscriptionStatus.PENDING,
        )

        topic4 = SubscriptionTopic.objects.create(
            name="Another Newsletter",
            slug="another-newsletter",
            description="Another newsletter",
            category="news",
        )
        UserSubscription.objects.create(
            user=self.user,
            topic=topic4,
            status=UserSubscription.SubscriptionStatus.UNSUBSCRIBED,
        )

        result = get_user_subscription_summary(self.user)

        assert result["total"] == 4
        assert result["active"] == 2
        assert result["pending"] == 1
        assert result["unsubscribed"] == 1

        assert "news" in result["by_category"]
        assert "tech" in result["by_category"]

        assert result["by_category"]["news"]["total"] == 3
        assert result["by_category"]["news"]["active"] == 1

        assert result["by_category"]["tech"]["total"] == 1
        assert result["by_category"]["tech"]["active"] == 1

    def test_get_user_subscription_summary_different_user(self):
        other_user = UserAccountFactory()

        UserSubscription.objects.create(
            user=self.user,
            topic=self.topic,
            status=UserSubscription.SubscriptionStatus.ACTIVE,
        )

        result = get_user_subscription_summary(other_user)

        assert result["total"] == 0
        assert result["active"] == 0
        assert result["by_category"] == {}

    @patch("user.utils.subscription.logger")
    def test_send_subscription_confirmation_logs_warning_already_active(
        self, mock_logger
    ):
        with patch(
            "user.utils.subscription.check_subscription_before_send",
            return_value=True,
        ):
            subscription = UserSubscription.objects.create(
                user=self.user,
                topic=self.topic,
                status=UserSubscription.SubscriptionStatus.PENDING,
                confirmation_token="test-token",
            )

            send_subscription_confirmation(subscription, self.user)

            mock_logger.warning.assert_called_once()
            assert (
                "already active subscription"
                in mock_logger.warning.call_args[0][0]
            )

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

        send_subscription_confirmation(subscription, self.user)

        mock_logger.warning.assert_called_once()
        assert (
            "already active subscription" in mock_logger.warning.call_args[0][0]
        )

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

        send_subscription_confirmation(subscription, self.user)

        mock_logger.error.assert_called_once()
        assert "No confirmation token" in mock_logger.error.call_args[0][0]
