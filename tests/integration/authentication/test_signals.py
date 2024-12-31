from unittest.mock import Mock, patch

from allauth.socialaccount.models import SocialAccount, SocialLogin
from django.test import TestCase

from authentication.signals import populate_profile


class PopulateProfileTest(TestCase):
    def setUp(self):
        self.user = Mock()
        self.sociallogin = SocialLogin(
            account=SocialAccount(provider="facebook", uid="123")
        )
        self.sociallogin.account.extra_data = {"picture": "http://example.com"}

    def test_populate_profile_no_sociallogin_or_user(self):
        populate_profile(None, None)
        self.assertFalse(self.user.image.save.called)

    def test_populate_profile_facebook_provider(self):
        with patch("requests.get") as mocked_get:
            mocked_get.return_value.status_code = 200
            mocked_get.return_value.content = b"image_content"
            populate_profile(self.sociallogin, self.user)
            self.assertTrue(self.user.image.save.called)

    def test_populate_profile_google_provider(self):
        self.sociallogin.account.provider = "google"
        with patch("requests.get") as mocked_get:
            mocked_get.return_value.status_code = 200
            mocked_get.return_value.content = b"image_content"
            populate_profile(self.sociallogin, self.user)
            self.assertTrue(self.user.image.save.called)
