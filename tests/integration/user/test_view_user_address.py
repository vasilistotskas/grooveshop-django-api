from __future__ import annotations

import json

from rest_framework import status
from rest_framework.test import APITestCase

from country.models import Country
from region.models import Region
from user.models import UserAccount
from user.models.address import UserAddress
from user.serializers.address import UserAddressSerializer


class UserAddressViewSetTestCase(APITestCase):
    user: UserAccount
    user_address: UserAddress

    def setUp(self):
        self.user = UserAccount.objects.create_user(
            email="test@test.com", password="test12345@!"
        )
        self.user_address = UserAddress.objects.create(
            user=self.user,
            title="test",
            first_name="test",
            last_name="test",
            street="test",
            street_number="test",
            city="test",
            zipcode="test",
            is_main=False,
        )
        self.client.force_authenticate(user=self.user)

    def test_list(self):
        response = self.client.get("/api/v1/user/address/")
        user_addresses = UserAddress.objects.all()
        serializer = UserAddressSerializer(user_addresses, many=True)
        self.assertEqual(response.data["results"], serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_create_valid(self):
        country = Country.objects.create(
            alpha_2="CY",
            alpha_3="CYP",
            name="Cyprus",
            iso_cc=123,
            phone_code=423,
        )
        region = Region.objects.create(
            alpha="CY-I",
            alpha_2=country,
            name="Cyprus Region",
        )
        payload = {
            "user": self.user.pk,
            "title": "test",
            "first_name": "test",
            "last_name": "test",
            "street": "test",
            "street_number": "test",
            "city": "test",
            "zipcode": "test",
            "is_main": False,
            "country": country.pk,
            "region": region.pk,
        }
        response = self.client.post(
            "/api/v1/user/address/",
            json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_invalid(self):
        payload = {
            "user": "INVALID",
            "title": "INVALID",
            "first_name": "INVALID",
            "last_name": "INVALID",
            "street": "INVALID",
            "street_number": "INVALID",
            "city": "INVALID",
            "zipcode": "INVALID",
            "is_main": "INVALID",
        }
        response = self.client.post(
            "/api/v1/user/address/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_retrieve_valid(self):
        response = self.client.get(f"/api/v1/user/address/{self.user_address.pk}/")
        user_address = UserAddress.objects.get(pk=self.user_address.pk)
        serializer = UserAddressSerializer(user_address)
        self.assertEqual(response.data, serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_retrieve_invalid(self):
        response = self.client.get(f"/api/v1/user/address/{self.user_address.pk + 1}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_valid(self):
        country = Country.objects.create(
            alpha_2="GR",
            alpha_3="GRC",
            name="Greece",
            iso_cc=300,
            phone_code=30,
        )
        region = Region.objects.create(
            alpha="GR-I",
            alpha_2=country,
            name="Central Greece",
        )
        payload = {
            "user": self.user.pk,
            "title": "test",
            "first_name": "test",
            "last_name": "test",
            "street": "test",
            "street_number": "test",
            "city": "test",
            "zipcode": "test",
            "is_main": False,
            "country": country.pk,
            "region": region.pk,
        }
        response = self.client.put(
            f"/api/v1/user/address/{self.user_address.pk}/",
            json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_invalid(self):
        payload = {
            "user": "INVALID",
            "title": "INVALID",
            "first_name": "INVALID",
            "last_name": "INVALID",
            "street": "INVALID",
            "street_number": "INVALID",
            "city": "INVALID",
            "zipcode": "INVALID",
            "is_main": False,
        }
        response = self.client.put(
            f"/api/v1/user/address/{self.user_address.pk}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_partial_update_valid(self):
        payload = {
            "user": self.user.pk,
            "title": "test",
            "is_main": False,
        }
        response = self.client.patch(
            f"/api/v1/user/address/{self.user_address.pk}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_partial_update_invalid(self):
        payload = {"user": "INVALID", "is_main": "INVALID"}
        response = self.client.patch(
            f"/api/v1/user/address/{self.user_address.pk}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_destroy_valid(self):
        response = self.client.delete(f"/api/v1/user/address/{self.user_address.pk}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_destroy_invalid(self):
        response = self.client.delete(
            f"/api/v1/user/address/{self.user_address.pk + 1}/"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_destroy_main_address(self):
        user_address = UserAddress.objects.create(
            user=self.user,
            title="test",
            first_name="test",
            last_name="test",
            street="test",
            street_number="test",
            city="test",
            zipcode="test",
            is_main=True,
        )
        response = self.client.delete(f"/api/v1/user/address/{user_address.pk}/")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
