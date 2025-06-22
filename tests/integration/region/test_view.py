from django.conf import settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from country.factories import CountryFactory
from region.factories import RegionFactory
from region.models import Region
from region.serializers import RegionDetailSerializer

languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE


class RegionViewSetTestCase(APITestCase):
    def setUp(self):
        self.country = CountryFactory(num_regions=0)
        self.region = RegionFactory(
            alpha="GRC",
            country=self.country,
        )

    def get_region_detail_url(self, pk):
        return reverse("region-detail", args=[pk])

    def get_region_list_url(self):
        return reverse("region-list")

    def test_list(self):
        url = self.get_region_list_url()
        response = self.client.get(url)
        regions = Region.objects.all()
        serializer = RegionDetailSerializer(regions, many=True)

        self.assertEqual(response.data["results"], serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_create_valid(self):
        payload = {
            "alpha": "GRD",
            "country": self.region.country.pk,
            "translations": {},
        }
        for language in languages:
            language_code = language[0]
            language_name = language[1]

            translation_payload = {
                "name": f"New Region name in {language_name}",
            }

            payload["translations"][language_code] = translation_payload

        url = self.get_region_list_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_invalid(self):
        payload = {
            "alpha": "invalid_alpha",
            "country": "invalid_country",
            "translations": {
                "invalid_lang_code": {
                    "name": "Translation for invalid language code",
                }
            },
        }

        url = self.get_region_list_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_retrieve_valid(self):
        url = self.get_region_detail_url(self.region.alpha)
        response = self.client.get(url)
        region = Region.objects.get(alpha=self.region.alpha)
        serializer = RegionDetailSerializer(region)

        self.assertEqual(response.data, serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_retrieve_invalid(self):
        url = self.get_region_detail_url(999)
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_valid(self):
        payload = {
            "alpha": "GRC",
            "country": self.region.country.pk,
            "translations": {},
        }
        for language in languages:
            language_code = language[0]
            language_name = language[1]

            translation_payload = {
                "name": f"Updated Region name in {language_name}",
            }

            payload["translations"][language_code] = translation_payload

        url = self.get_region_detail_url(self.region.pk)
        response = self.client.put(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_invalid(self):
        payload = {
            "alpha": "invalid_alpha",
            "country": "invalid_country",
            "translations": {
                "invalid_lang_code": {
                    "name": "Translation for invalid language code",
                }
            },
        }

        url = self.get_region_detail_url(self.region.pk)
        response = self.client.put(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_partial_update_valid(self):
        payload = {
            "translations": {
                default_language: {
                    "name": f"Updated Region {default_language}",
                }
            },
        }

        url = self.get_region_detail_url(self.region.pk)
        response = self.client.patch(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_partial_update_invalid(self):
        payload = {
            "alpha": "invalid_alpha",
            "translations": {
                "invalid_lang_code": {
                    "name": "Translation for invalid language code",
                }
            },
        }

        url = self.get_region_detail_url(self.region.pk)
        response = self.client.patch(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_destroy_valid(self):
        url = self.get_region_detail_url(self.region.pk)
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_destroy_invalid(self):
        url = self.get_region_detail_url(999)
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
