from __future__ import annotations

import json

from rest_framework import status
from rest_framework.test import APITestCase

from user.models import UserAccount
from user.serializers.account import UserAccountSerializer


class UserAccountViewSetTestCase(APITestCase):
    user_account: UserAccount

    def setUp(self):
        self.user_account = UserAccount.objects.create_user(
            email="test@test.com", password="test12345@!"
        )

    def test_list(self):
        response = self.client.get("/api/v1/user/account/")
        user_accounts = UserAccount.objects.all()
        serializer = UserAccountSerializer(user_accounts, many=True)
        self.assertEqual(response.data["results"], serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_create_valid(self):
        payload = {
            "email": "test_one@test.com",
            "password": "test12345@!",
            "first_name": "test",
            "last_name": "test",
        }
        response = self.client.post(
            "/api/v1/user/account/",
            json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_invalid(self):
        payload = {"email": "INVALID", "password": "INVALID"}
        response = self.client.post(
            "/api/v1/user/account/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_retrieve_valid(self):
        response = self.client.get(f"/api/v1/user/account/{self.user_account.pk}/")
        user_account = UserAccount.objects.get(pk=self.user_account.pk)
        serializer = UserAccountSerializer(user_account)
        self.assertEqual(response.data, serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_retrieve_invalid(self):
        response = self.client.get(f"/api/v1/user/account/{self.user_account.pk + 1}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_valid(self):
        payload = {
            "email": "test_one@test.com",
            "password": "test12345@!",
            "first_name": "test",
            "last_name": "test",
        }
        response = self.client.put(
            f"/api/v1/user/account/{self.user_account.pk}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_invalid(self):
        payload = {"email": "INVALID", "password": "INVALID"}
        response = self.client.put(
            f"/api/v1/user/account/{self.user_account.pk}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_partial_update_valid(self):
        payload = {
            "first_name": "test",
        }
        response = self.client.patch(
            f"/api/v1/user/account/{self.user_account.pk}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_partial_update_invalid(self):
        payload = {"email": "INVALID", "password": "INVALID"}
        response = self.client.patch(
            f"/api/v1/user/account/{self.user_account.pk}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_destroy_valid(self):
        user_account = UserAccount.objects.create_user(
            email="test_two@test.com", password="test12345@!"
        )
        response = self.client.delete(f"/api/v1/user/account/{user_account.pk}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_destroy_invalid(self):
        response = self.client.delete(
            f"/api/v1/user/account/{self.user_account.pk + 1}/"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
