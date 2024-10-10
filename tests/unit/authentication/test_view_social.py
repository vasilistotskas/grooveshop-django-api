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
    @mock.patch.object(SocialLogin, "is_existing", new_callable=mock.PropertyMock)
    def test_pre_social_login_existent(self, mock_is_existing, mock_pre_social_login):
        request = mock.Mock()
        sociallogin = SocialLogin()
        mock_is_existing.return_value = True
        self.adapter.pre_social_login(request, sociallogin)
        mock_pre_social_login.assert_not_called()

    @mock.patch.object(DefaultSocialAccountAdapter, "pre_social_login")
    @mock.patch.object(User.objects, "filter")
    def test_pre_social_login_nonexistent(self, mock_filter, mock_pre_social_login):
        request = mock.Mock()

        mock_queryset = mock.Mock()

        mock_user = mock.Mock()
        mock_queryset.first.return_value = mock_user
        mock_filter.return_value = mock_queryset

        sociallogin = mock.Mock()
        sociallogin.is_existing = False
        sociallogin.account.extra_data = {"email": "test@example.com"}

        mock_email_addresses = mock.Mock()
        mock_email_addresses.configure_mock(
            **{
                "__iter__": mock.Mock(
                    return_value=iter([mock.Mock(verified=True, email="test@example.com")])
                )
            }
        )
        sociallogin.email_addresses = mock_email_addresses

        self.adapter.pre_social_login(request, sociallogin)
        sociallogin.connect.assert_called_once()
