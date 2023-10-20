from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from country.models import Country
from helpers.seed import get_or_create_default_image
from region.models import Region
from user.models.address import UserAddress
from user.serializers.address import UserAddressSerializer

languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE
User = get_user_model()


@override_settings(
    STORAGES={
        "default": {
            "BACKEND": "django.core.files.storage.memory.InMemoryStorage",
        },
    }
)
class UserAddressViewSetTestCase(APITestCase):
    user: User = None
    address: UserAddress = None

    def setUp(self):
        self.user = User.objects.create_user(
            email="test@test.com", password="test12345@!"
        )

        image_flag = get_or_create_default_image("uploads/region/no_photo.jpg")
        self.country = Country.objects.create(
            alpha_2="GR",
            alpha_3="GRC",
            iso_cc=300,
            phone_code=30,
            image_flag=image_flag,
        )
        self.region = Region.objects.create(
            alpha="GRC",
            alpha_2=self.country,
        )
        for language in languages:
            self.region.set_current_language(language)
            self.region.name = f"Region {language}"
            self.region.save()
        self.region.set_current_language(default_language)

        # Login to authenticate
        self.client.login(email="test@test.com", password="test12345@!")

        self.address = UserAddress.objects.create(
            user=self.user,
            country=self.country,
            region=self.region,
            title="test",
            first_name="test",
            last_name="test",
            street="test",
            street_number="test",
            city="test",
            zipcode="test",
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
        user_address = UserAddress.objects.get(pk=self.address.pk)
        serializer = UserAddressSerializer(user_address)

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
        invalid_user_address_id = 9999  # An ID that doesn't exist in the database
        url = self.get_user_address_detail_url(invalid_user_address_id)
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def tearDown(self) -> None:
        super().tearDown()
        self.user.delete()
        self.address.delete()
        self.client.logout()
