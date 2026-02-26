from unittest import mock

from django.test import TestCase, override_settings

from user.adapter import SocialAccountAdapter


class TestSocialAccountAdapter(TestCase):
    def setUp(self):
        self.adapter = SocialAccountAdapter()

    def test_get_connect_redirect_url_with_post_next(self):
        request = mock.Mock()
        request.POST.get.return_value = "/account/settings"
        request.GET.get.return_value = None
        social_account = mock.Mock()

        url = self.adapter.get_connect_redirect_url(request, social_account)

        self.assertEqual(url, "/account/settings")

    def test_get_connect_redirect_url_with_get_next(self):
        request = mock.Mock()
        request.POST.get.return_value = None
        request.GET.get.return_value = "/account/providers"
        social_account = mock.Mock()

        url = self.adapter.get_connect_redirect_url(request, social_account)

        self.assertEqual(url, "/account/providers")

    @override_settings(NUXT_BASE_URL="https://example.com")
    def test_get_connect_redirect_url_defaults_to_account(self):
        request = mock.Mock()
        request.POST.get.return_value = None
        request.GET.get.return_value = None
        social_account = mock.Mock()

        url = self.adapter.get_connect_redirect_url(request, social_account)

        self.assertEqual(url, "https://example.com/account")

    def test_post_next_takes_priority_over_get_next(self):
        request = mock.Mock()
        request.POST.get.return_value = "/from-post"
        request.GET.get.return_value = "/from-get"
        social_account = mock.Mock()

        url = self.adapter.get_connect_redirect_url(request, social_account)

        self.assertEqual(url, "/from-post")
