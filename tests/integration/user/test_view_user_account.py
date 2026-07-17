from __future__ import annotations

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from user.serializers.account import UserDetailsSerializer
from core.utils.testing import TestURLFixerMixin
from user.factories.account import UserAccountFactory

User = get_user_model()


class UserAccountViewSetTestCase(TestURLFixerMixin, APITestCase):
    def setUp(self):
        self.user = UserAccountFactory(
            is_superuser=True,
            is_staff=True,
        )
        self.client.force_authenticate(user=self.user)

    def get_user_account_detail_url(self, pk):
        return reverse("user-account-detail", kwargs={"pk": pk})

    def get_user_account_list_url(self):
        return reverse("user-account-list")

    def test_list(self):
        url = self.get_user_account_list_url()
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        if isinstance(response.data, dict) and "results" in response.data:
            users = User.objects.all()
            serializer = UserDetailsSerializer(
                users, many=True, context={"request": response.wsgi_request}
            )
            self.assertEqual(response.data["results"], serializer.data)
        else:
            users = User.objects.all()
            serializer = UserDetailsSerializer(
                users, many=True, context={"request": response.wsgi_request}
            )
            self.assertEqual(response.data, serializer.data)

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
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertIn("pk", response.data)
        self.assertEqual(response.data["pk"], self.user.id)
        self.assertIn("email", response.data)
        self.assertEqual(response.data["email"], self.user.email)
        self.assertIn("username", response.data)
        self.assertEqual(response.data["username"], self.user.username)

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

    def test_partial_update_cannot_change_email(self):
        # Email is read-only on the profile serializer: changing it must go
        # through allauth's verification flow, so a supplied email is ignored
        # (not honoured and not a 400 for a malformed value).
        original_email = self.user.email
        url = self.get_user_account_detail_url(self.user.pk)

        response = self.client.patch(
            url, data={"email": "attacker@evil.com"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, original_email)

    def test_raw_delete_not_allowed(self):
        # A raw hard-delete would sever Order.user while leaving the
        # customer's PII on the order and in carrier shipment metadata, so
        # DELETE on the detail route is not exposed. Account deletion must
        # go through the GDPR-compliant ``delete_account`` action.
        url = self.get_user_account_detail_url(self.user.pk)
        response = self.client.delete(url)

        self.assertEqual(
            response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED
        )
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())
