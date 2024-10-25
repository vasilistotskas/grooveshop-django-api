from django.conf import settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from country.factories import CountryFactory
from country.models import Country
from country.serializers import CountrySerializer

languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE


class CountryViewSetTestCase(APITestCase):
    country: Country = None

    def setUp(self):
        self.country = CountryFactory(alpha_2="GR", alpha_3="GRC", iso_cc=301, phone_code=30, num_regions=0)

    @staticmethod
    def get_country_detail_url(pk):
        return reverse("country-detail", args=[pk])

    @staticmethod
    def get_country_list_url():
        return reverse("country-list")

    def test_list(self):
        url = self.get_country_list_url()
        response = self.client.get(url)
        countries = Country.objects.all()
        serializer = CountrySerializer(countries, many=True)

        self.assertEqual(response.data["results"], serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_create_valid(self):
        payload = {
            "alpha_2": "CY",
            "alpha_3": "CYP",
            "translations": {},
            "iso_cc": 196,
            "phone_code": 357,
        }

        for language in languages:
            language_code = language[0]
            language_name = language[1]

            translation_payload = {
                "name": f"New Country name in {language_name}",
            }

            payload["translations"][language_code] = translation_payload

        url = self.get_country_list_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_invalid(self):
        payload = {
            "alpha_2": "invalid_alpha_2",
            "alpha_3": "invalid_alpha_3",
            "name": "invalid_alpha_name",
            "iso_cc": "invalid_iso_cc",
            "phone_code": "invalid_phone_code",
            "translations": {
                "invalid_lang_code": {
                    "name": "Translation for invalid language code",
                }
            },
        }

        url = self.get_country_list_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_retrieve_valid(self):
        url = self.get_country_detail_url(self.country.pk)
        response = self.client.get(url)
        country = Country.objects.get(pk=self.country.pk)
        serializer = CountrySerializer(country)

        self.assertEqual(response.data, serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_retrieve_invalid(self):
        invalid_country_id = 9999
        url = self.get_country_detail_url(invalid_country_id)
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_valid(self):
        payload = {
            "alpha_2": "GR",
            "alpha_3": "GRC",
            "translations": {},
            "iso_cc": 300,
            "phone_code": 30,
        }

        for language in languages:
            language_code = language[0]
            language_name = language[1]

            translation_payload = {
                "name": f"Updated Country name in {language_name}",
            }

            payload["translations"][language_code] = translation_payload

        url = self.get_country_detail_url(self.country.pk)
        response = self.client.put(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_invalid(self):
        payload = {
            "alpha_2": "invalid_alpha_2",
            "alpha_3": "invalid_alpha_3",
            "name": "invalid_name",
            "iso_cc": "invalid_iso_cc",
            "phone_code": "invalid_phone_code",
            "translations": {
                "invalid_lang_code": {
                    "name": "Translation for invalid language code",
                }
            },
        }

        url = self.get_country_detail_url(self.country.pk)
        response = self.client.put(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_partial_update_valid(self):
        payload = {
            "name": "Cyprus",
            "translations": {
                default_language: {
                    "name": "Cyprus",
                }
            },
        }

        url = self.get_country_detail_url(self.country.pk)
        response = self.client.patch(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_partial_update_invalid(self):
        payload = {
            "alpha_3": "GRRCC",
            "translations": {
                "invalid_lang_code": {
                    "name": "Translation for invalid language code",
                }
            },
        }

        url = self.get_country_detail_url(self.country.pk)
        response = self.client.patch(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_destroy_valid(self):
        url = self.get_country_detail_url(self.country.pk)
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Country.objects.filter(pk=self.country.pk).exists())

    def test_destroy_invalid(self):
        invalid_country_id = 9999
        url = self.get_country_detail_url(invalid_country_id)
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
