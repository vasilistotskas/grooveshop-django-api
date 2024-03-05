from __future__ import annotations

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from authentication.serializers import AuthenticationSerializer

User = get_user_model()


class UserAccountViewSetTestCase(APITestCase):
    user: User = None

    def setUp(self):
        self.user = User.objects.create_user(
            email="test@test.com",
            password="test12345@!",
            is_superuser=True,
            is_staff=True,
        )

        self.client.login(email="test@test.com", password="test12345@!")
        self.client.force_authenticate(user=self.user)

    @staticmethod
    def get_user_account_detail_url(pk):
        return reverse("user-account-detail", kwargs={"pk": pk})

    @staticmethod
    def get_user_account_list_url():
        return reverse("user-account-list")

    def test_list(self):
        url = self.get_user_account_list_url()
        response = self.client.get(url)
        user_accounts = User.objects.all()
        serializer = AuthenticationSerializer(user_accounts, many=True)

        self.assertEqual(response.data["results"], serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_create_valid(self):
        payload = {
            "email": "test2@test.com",
            "password": "test12345@!",
        }
        url = self.get_user_account_list_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_invalid(self):
        payload = {
            "email": "invalid_email",
            "password": "test12345@!",
        }
        url = self.get_user_account_list_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_retrieve_valid(self):
        url = self.get_user_account_detail_url(self.user.id)
        response = self.client.get(url)
        user_accounts = User.objects.get(id=self.user.id)
        serializer = AuthenticationSerializer(user_accounts)

        self.assertEqual(response.data, serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_retrieve_invalid(self):
        invalid_user_account_id = 9999
        url = self.get_user_account_detail_url(invalid_user_account_id)
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_valid(self):
        payload = {
            "email": "test@test.com",
            "first_name": "test",
            "last_name": "test",
            "phone": "2101234567",
            "city": "test",
            "zipcode": "12345",
            "address": "test",
            "place": "test",
            "country": None,
            "region": None,
        }

        url = self.get_user_account_detail_url(self.user.pk)
        response = self.client.put(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_invalid(self):
        payload = {
            "email": "invalid_email",
            "first_name": "invalid_first_name",
            "last_name": "invalid_last_name",
            "phone": "invalid_phone",
            "city": "invalid_city",
            "zipcode": "invalid_zipcode",
            "address": "invalid_address",
            "place": "invalid_place",
            "country": "invalid_country",
            "region": "invalid_region",
        }

        url = self.get_user_account_detail_url(self.user.pk)
        response = self.client.put(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_partial_update_valid(self):
        payload = {
            "first_name": "test_partial_update",
            "last_name": "test_partial_update",
        }

        url = self.get_user_account_detail_url(self.user.pk)
        response = self.client.patch(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_partial_update_invalid(self):
        payload = {
            "email": "invalid_email",
        }

        url = self.get_user_account_detail_url(self.user.pk)
        response = self.client.patch(url, data=payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_destroy_valid(self):
        url = self.get_user_account_detail_url(self.user.pk)
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(User.objects.filter(pk=self.user.pk).exists())

    def test_destroy_invalid(self):
        invalid_user_account_id = 9999
        url = self.get_user_account_detail_url(invalid_user_account_id)
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def tearDown(self) -> None:
        super().tearDown()
        self.user.delete()
