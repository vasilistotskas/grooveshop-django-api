from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings

from user.factories.account import UserAccountFactory
from user.models.subscription import SubscriptionTopic, UserSubscription
from user.utils.subscription import (
    check_subscription_before_send,
    generate_unsubscribe_link,
    get_user_subscription_summary,
    send_newsletter,
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
        SUBSCRIPTION_CONFIRMATION_URL="https://example.com/confirm/{token}/",
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
        mock_render.assert_called_once()
        mock_email_class.assert_called_once()
        mock_email.attach_alternative.assert_called_once()
        mock_email.send.assert_called_once()

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
        SUBSCRIPTION_CONFIRMATION_URL="https://example.com/confirm/{token}/",
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

    @override_settings(SITE_URL="https://test-site.com")
    @patch("user.utils.subscription.default_token_generator")
    @patch("user.utils.subscription.urlsafe_base64_encode")
    def test_generate_unsubscribe_link(self, mock_encode, mock_token_gen):
        mock_token_gen.make_token.return_value = "test-token"
        mock_encode.return_value = "encoded-uid"

        result = generate_unsubscribe_link(self.user, self.topic)

        expected_url = "https://test-site.com/unsubscribe/encoded-uid/test-token/test-newsletter/"
        assert result == expected_url
        mock_token_gen.make_token.assert_called_once_with(self.user)
        mock_encode.assert_called_once()

    @override_settings(SITE_URL="")
    @patch("user.utils.subscription.default_token_generator")
    @patch("user.utils.subscription.urlsafe_base64_encode")
    def test_generate_unsubscribe_link_no_site_url(
        self, mock_encode, mock_token_gen
    ):
        mock_token_gen.make_token.return_value = "test-token"
        mock_encode.return_value = "encoded-uid"

        result = generate_unsubscribe_link(self.user, self.topic)

        expected_url = "/unsubscribe/encoded-uid/test-token/test-newsletter/"
        assert result == expected_url

    @override_settings(
        DEFAULT_FROM_EMAIL="noreply@test.com", SITE_URL="https://test-site.com"
    )
    @patch("user.utils.subscription.render_to_string")
    @patch("user.utils.subscription.EmailMultiAlternatives")
    @patch("user.utils.subscription.generate_unsubscribe_link")
    def test_send_newsletter_success(
        self, mock_unsubscribe, mock_email_class, mock_render
    ):
        mock_render.return_value = "<html>Newsletter content</html>"
        mock_unsubscribe.return_value = "https://test.com/unsubscribe/123/"
        mock_email = MagicMock()
        mock_email_class.return_value = mock_email

        user2 = UserAccountFactory()
        UserSubscription.objects.create(
            user=self.user,
            topic=self.topic,
            status=UserSubscription.SubscriptionStatus.ACTIVE,
        )
        UserSubscription.objects.create(
            user=user2,
            topic=self.topic,
            status=UserSubscription.SubscriptionStatus.ACTIVE,
        )

        context = {"newsletter_title": "Test Newsletter"}

        result = send_newsletter(
            topic=self.topic,
            subject="Test Subject",
            template_name="newsletter/test.html",
            context=context,
            batch_size=50,
        )

        assert result["sent"] == 2
        assert result["failed"] == 0
        assert result["skipped"] == 0
        assert mock_email.send.call_count == 2

    @override_settings(
        DEFAULT_FROM_EMAIL="noreply@test.com", SITE_URL="https://test-site.com"
    )
    @patch("user.utils.subscription.render_to_string")
    @patch("user.utils.subscription.EmailMultiAlternatives")
    @patch("user.utils.subscription.generate_unsubscribe_link")
    def test_send_newsletter_with_inactive_user(
        self, mock_unsubscribe, mock_email_class, mock_render
    ):
        mock_render.return_value = "<html>Newsletter content</html>"
        mock_unsubscribe.return_value = "https://test.com/unsubscribe/123/"
        mock_email = MagicMock()
        mock_email_class.return_value = mock_email

        inactive_user = UserAccountFactory(is_active=False)
        UserSubscription.objects.create(
            user=inactive_user,
            topic=self.topic,
            status=UserSubscription.SubscriptionStatus.ACTIVE,
        )

        result = send_newsletter(
            topic=self.topic,
            subject="Test Subject",
            template_name="newsletter/test.html",
            context={},
        )

        assert result["sent"] == 0
        assert result["failed"] == 0
        assert result["skipped"] == 1
        mock_email.send.assert_not_called()

    @override_settings(
        DEFAULT_FROM_EMAIL="noreply@test.com", SITE_URL="https://test-site.com"
    )
    @patch("user.utils.subscription.render_to_string")
    @patch("user.utils.subscription.EmailMultiAlternatives")
    @patch("user.utils.subscription.generate_unsubscribe_link")
    def test_send_newsletter_email_exception(
        self, mock_unsubscribe, mock_email_class, mock_render
    ):
        mock_render.return_value = "<html>Newsletter content</html>"
        mock_unsubscribe.return_value = "https://test.com/unsubscribe/123/"
        mock_email = MagicMock()
        mock_email.send.side_effect = Exception("Email failed")
        mock_email_class.return_value = mock_email

        UserSubscription.objects.create(
            user=self.user,
            topic=self.topic,
            status=UserSubscription.SubscriptionStatus.ACTIVE,
        )

        result = send_newsletter(
            topic=self.topic,
            subject="Test Subject",
            template_name="newsletter/test.html",
            context={},
        )

        assert result["sent"] == 0
        assert result["failed"] == 1
        assert result["skipped"] == 0

    def test_send_newsletter_no_active_subscriptions(self):
        UserSubscription.objects.create(
            user=self.user,
            topic=self.topic,
            status=UserSubscription.SubscriptionStatus.PENDING,
        )

        result = send_newsletter(
            topic=self.topic,
            subject="Test Subject",
            template_name="newsletter/test.html",
            context={},
        )

        assert result["sent"] == 0
        assert result["failed"] == 0
        assert result["skipped"] == 0

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

    @patch("user.utils.subscription.logger")
    def test_send_newsletter_logs_stats(self, mock_logger):
        UserSubscription.objects.create(
            user=self.user,
            topic=self.topic,
            status=UserSubscription.SubscriptionStatus.ACTIVE,
        )

        with (
            patch("user.utils.subscription.render_to_string"),
            patch("user.utils.subscription.EmailMultiAlternatives"),
            patch("user.utils.subscription.generate_unsubscribe_link"),
        ):
            send_newsletter(
                topic=self.topic,
                subject="Test",
                template_name="test.html",
                context={},
            )

            mock_logger.info.assert_called()
            log_message = mock_logger.info.call_args[0][0]
            assert "Newsletter sent for topic" in log_message
            assert "1 sent, 0 failed, 0 skipped" in log_message
