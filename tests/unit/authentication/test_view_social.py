from unittest import mock

from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.socialaccount.models import SocialLogin
from django.contrib.auth import get_user_model
from django.test import TestCase

from user.adapter import SocialAccountAdapter

User = get_user_model()


class TestSocialAccountAdapter(TestCase):
    def setUp(self):
        self.adapter = SocialAccountAdapter()

    @mock.patch.object(DefaultSocialAccountAdapter, "pre_social_login")
    @mock.patch.object(
        SocialLogin, "is_existing", new_callable=mock.PropertyMock
    )
    def test_pre_social_login_existent(
        self, mock_is_existing, mock_pre_social_login
    ):
        request = mock.Mock()
        sociallogin = SocialLogin()
        mock_is_existing.return_value = True
        self.adapter.pre_social_login(request, sociallogin)
        mock_pre_social_login.assert_not_called()

    @mock.patch.object(DefaultSocialAccountAdapter, "pre_social_login")
    def test_pre_social_login_nonexistent(self, mock_pre_social_login):
        request = mock.Mock()

        sociallogin = mock.Mock()

        is_existing_property = mock.PropertyMock(return_value=False)
        type(sociallogin).is_existing = is_existing_property

        sociallogin.account = mock.Mock()
        sociallogin.account.extra_data = {"email": "test@example.com"}

        email = mock.Mock()
        email.verified = True
        email.email = "test@example.com"
        sociallogin.email_addresses = [email]

        mock_user = mock.Mock()

        mock_filter = mock.Mock()
        mock_filter.first.return_value = mock_user

        def side_effect(req, user):
            pass

        sociallogin.connect.side_effect = side_effect

        with mock.patch.object(
            User.objects, "filter", return_value=mock_filter
        ):
            self.adapter.pre_social_login(request, sociallogin)

            sociallogin.connect.assert_called_once_with(request, mock_user)
