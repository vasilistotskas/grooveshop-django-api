from django.conf import settings
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from country.models import Country
from helpers.seed import get_or_create_default_image
from region.models import Region
from region.serializers import RegionSerializer

languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE


@override_settings(
    STORAGES={
        "default": {
            "BACKEND": "django.core.files.storage.memory.InMemoryStorage",
        },
    }
)
class RegionViewSetTestCase(APITestCase):
    region: Region = None
    country: Country = None

    def setUp(self):
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
            country=self.country,
        )
        for language in languages:
            self.region.set_current_language(language)
            self.region.name = f"Region {language}"
            self.region.save()
        self.region.set_current_language(default_language)

    @staticmethod
    def get_region_detail_url(pk):
        return reverse("region-detail", args=[pk])

    @staticmethod
    def get_region_list_url():
        return reverse("region-list")

    def test_list(self):
        url = self.get_region_list_url()
        response = self.client.get(url)
        regions = Region.objects.all()
        serializer = RegionSerializer(regions, many=True)

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
        serializer = RegionSerializer(region)

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

    def tearDown(self) -> None:
        super().tearDown()
        self.region.delete()
        self.country.delete()
