from allauth.socialaccount.models import SocialAccount
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.reverse import reverse
from rest_framework.test import APITestCase

from authentication.views.base import AuthSocialAccountListView

User = get_user_model()


class AuthSocialAccountListViewTest(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="testuser@example.com", password="testpassword"
        )
        SocialAccount.objects.create(uid="123", provider="twitter", user=self.user)

    def test_get_queryset_with_swagger_fake_view(self):
        view = AuthSocialAccountListView()
        view.swagger_fake_view = True
        queryset = view.get_queryset()
        self.assertEqual(queryset.count(), 0)


class IsUserRegisteredTest(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="testuser@example.com", password="testpassword"
        )

    def test_is_user_registered(self):
        response = self.client.post(
            reverse("is_user_registered"), data={"email": "testuser@example.com"}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["registered"], True)

    def test_is_user_not_registered(self):
        response = self.client.post(
            reverse("is_user_registered"), data={"email": "notregistered@example.com"}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["registered"], False)
