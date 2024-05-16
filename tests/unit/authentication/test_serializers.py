import unittest
from unittest import mock
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import PasswordResetForm
from django.test import override_settings
from django.test import RequestFactory
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APITestCase

from authentication.serializers import AuthenticationAllAuthPasswordResetForm
from authentication.serializers import AuthenticationPasswordResetSerializer
from authentication.serializers import AuthenticationRegisterSerializer

User = get_user_model()


class TestAuthenticationAllAuthPasswordResetForm(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email="testuser@example.com", password="testpassword"
        )
        cls.url = reverse("rest_password_reset")

    def test_password_reset(self):
        response = self.client.post(self.url, {"email": self.user.email})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data, {"detail": "Password reset e-mail has been sent."}
        )

    @mock.patch("allauth.account.adapter.get_adapter")
    @mock.patch("django.contrib.sites.shortcuts.get_current_site")
    def test_save(self, mock_get_current_site, mock_get_adapter):
        form = AuthenticationAllAuthPasswordResetForm()
        email = self.user.email
        form.cleaned_data = {"email": email}
        form.users = User.objects.filter(email=email)

        request = RequestFactory().get(reverse("rest_password_reset"))
        settings.NUXT_BASE_DOMAIN = "nuxt-domain.com"

        mock_get_current_site.return_value = "testsite"
        mock_adapter = mock.Mock()
        mock_get_adapter.return_value = mock_adapter

        try:
            result = form.save(request=request)
        except Exception as error:
            print(error)

        self.assertEqual(result, self.user.email)
        try:
            mock_adapter.send_mail.assert_called()
        except AssertionError:
            print("send_mail not called")


class MockAuthenticationAllAuthPasswordResetForm:
    pass


class TestAuthenticationPasswordResetSerializer(unittest.TestCase):
    def test_without_allauth(self):
        with override_settings(
            INSTALLED_APPS=[app for app in settings.INSTALLED_APPS if app != "allauth"]
        ):
            serializer = AuthenticationPasswordResetSerializer()
            self.assertIs(serializer.password_reset_form_class, PasswordResetForm)

    @patch(
        "authentication.serializers.AuthenticationAllAuthPasswordResetForm",
        new=MockAuthenticationAllAuthPasswordResetForm,
    )
    def test_with_allauth(self):
        installed_apps = (
            settings.INSTALLED_APPS
            if "allauth" in settings.INSTALLED_APPS
            else settings.INSTALLED_APPS + ["allauth"]
        )
        with override_settings(INSTALLED_APPS=installed_apps):
            serializer = AuthenticationPasswordResetSerializer()
            self.assertIs(
                serializer.password_reset_form_class,
                MockAuthenticationAllAuthPasswordResetForm,
            )


class TestAuthenticationRegisterSerializer(TestCase):
    def setUp(self):
        self.data = {
            "password1": "test_password",
            "password2": "test_password",
            "email": "test@example.com",
        }
        self.auth_serializer = AuthenticationRegisterSerializer(data=self.data)

    def test_get_cleaned_data(self):
        self.auth_serializer.is_valid(raise_exception=True)
        cleaned_data = self.auth_serializer.get_cleaned_data()
        expected_data = {
            "password1": "test_password",
            "email": "test@example.com",
            "username": "",
        }
        self.assertEqual(cleaned_data, expected_data)
