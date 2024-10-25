from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from country.factories import CountryFactory
from region.factories import RegionFactory
from user.factories.account import UserAccountFactory
from user.factories.address import UserAddressFactory
from user.models.address import UserAddress
from user.serializers.address import UserAddressSerializer

languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE
User = get_user_model()


class UserAddressViewSetTestCase(APITestCase):
    user: User = None
    address: UserAddress = None

    def setUp(self):
        self.user = UserAccountFactory(num_addresses=0)
        self.country = CountryFactory(alpha_2="GR", alpha_3="GRC", iso_cc=301, phone_code=30, num_regions=0)
        self.region = RegionFactory(alpha="GRC", country=self.country)
        self.client.force_authenticate(user=self.user)
        self.address = UserAddressFactory(
            user=self.user,
            country=self.country,
            region=self.region,
            is_main=False,
        )

    @staticmethod
    def get_user_address_detail_url(pk):
        return reverse("user-address-detail", kwargs={"pk": pk})

    @staticmethod
    def get_user_address_list_url():
        return reverse("user-address-list")

    @staticmethod
    def get_user_address_set_main_url(pk):
        return reverse("user-address-set-main", kwargs={"pk": pk})

    def test_list(self):
        url = self.get_user_address_list_url()
        response = self.client.get(url)
        user_addresses = UserAddress.objects.all()
        serializer = UserAddressSerializer(user_addresses, many=True)

        self.assertEqual(response.data["results"], serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_create_valid(self):
        payload = {
            "user": self.user.id,
            "country": self.country.alpha_2,
            "region": self.region.alpha,
            "phone": "2101234567",
            "title": "test",
            "first_name": "test",
            "last_name": "test",
            "street": "test",
            "street_number": "test",
            "city": "test",
            "zipcode": "test",
            "is_main": False,
        }

        url = self.get_user_address_list_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_invalid(self):
        payload = {
            "title": "invalid_title",
            "first_name": "invalid_first_name",
            "last_name": "invalid_last_name",
            "street": "invalid_street",
            "street_number": "invalid_street_number",
            "city": "invalid_city",
            "zipcode": "invalid_zipcode",
            "is_main": "invalid_is_main",
        }

        url = self.get_user_address_list_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_retrieve_valid(self):
        url = self.get_user_address_detail_url(self.address.pk)
        response = self.client.get(url)
        addresses = UserAddress.objects.get(pk=self.address.pk)
        serializer = UserAddressSerializer(addresses)

        self.assertEqual(response.data, serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_retrieve_invalid(self):
        invalid_user_address_id = 9999
        url = self.get_user_address_detail_url(invalid_user_address_id)
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_valid(self):
        payload = {
            "user": self.user.id,
            "country": self.country.alpha_2,
            "region": self.region.alpha,
            "phone": "2101234567",
            "title": "test",
            "first_name": "test",
            "last_name": "test",
            "street": "test",
            "street_number": "test",
            "city": "test",
            "zipcode": "test",
            "is_main": False,
        }

        url = self.get_user_address_detail_url(self.address.pk)
        response = self.client.put(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_invalid(self):
        payload = {
            "title": "invalid_title",
            "first_name": "invalid_first_name",
            "last_name": "invalid_last_name",
            "street": "invalid_street",
            "street_number": "invalid_street_number",
            "city": "invalid_city",
            "zipcode": "invalid_zipcode",
            "is_main": "invalid_is_main",
        }

        url = self.get_user_address_detail_url(self.address.pk)
        response = self.client.put(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_partial_update_valid(self):
        payload = {"title": "test"}

        url = self.get_user_address_detail_url(self.address.pk)
        response = self.client.patch(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_partial_update_invalid(self):
        payload = {
            "title": "invalid_title",
            "country": "invalid_country",
            "region": "invalid_region",
            "floor": "invalid_floor",
        }

        url = self.get_user_address_detail_url(self.address.pk)
        response = self.client.patch(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_destroy_valid(self):
        url = self.get_user_address_detail_url(self.address.pk)
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(UserAddress.objects.filter(pk=self.address.pk).exists())

    def test_destroy_invalid(self):
        invalid_user_address_id = 9999
        url = self.get_user_address_detail_url(invalid_user_address_id)
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
