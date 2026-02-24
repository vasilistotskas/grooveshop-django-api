from unittest.mock import Mock, patch

from allauth.socialaccount.models import SocialAccount, SocialLogin
from django.test import TestCase

from user.signals import populate_profile


class PopulateProfileTest(TestCase):
    def setUp(self):
        self.user = Mock()
        self.user.pk = 1
        self.sociallogin = SocialLogin(
            account=SocialAccount(provider="facebook", uid="123")
        )
        self.sociallogin.account.extra_data = {"picture": "http://example.com"}

    def test_populate_profile_no_sociallogin_or_user(self):
        populate_profile(None, None)

    @patch("user.tasks.download_social_avatar_task")
    def test_populate_profile_facebook_provider(self, mock_task):
        populate_profile(self.sociallogin, self.user)
        mock_task.delay.assert_called_once_with(
            user_id=self.user.pk,
            picture_url=f"http://graph.facebook.com/{self.sociallogin.account.uid}/picture?type=large",
        )

    @patch("user.tasks.download_social_avatar_task")
    def test_populate_profile_google_provider(self, mock_task):
        self.sociallogin.account.provider = "google"
        populate_profile(self.sociallogin, self.user)
        mock_task.delay.assert_called_once_with(
            user_id=self.user.pk,
            picture_url="http://example.com",
        )
