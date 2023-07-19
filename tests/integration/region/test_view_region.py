from __future__ import annotations

import json

from django.conf import settings
from rest_framework import status
from rest_framework.test import APITestCase

from country.models import Country
from region.models import Region
from region.serializers import RegionSerializer


class RegionViewSetTestCase(APITestCase):
    region: Region
    country: Country

    def setUp(self):
        self.country = Country.objects.create(
            alpha_2="GR",
            alpha_3="GRC",
            iso_cc=300,
            phone_code=30,
        )
        self.region = Region.objects.create(
            alpha="GR-I",
            alpha_2=self.country,
        )

    def test_list(self):
        response = self.client.get("/api/v1/region/")
        regions = Region.objects.all()
        serializer = RegionSerializer(regions, many=True)
        self.assertEqual(response.data["results"], serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_create_valid(self):
        country = Country.objects.create(
            alpha_2="CY",
            alpha_3="CYP",
            iso_cc=196,
            phone_code=357,
        )
        payload = {
            "translations": {},
            "alpha": "CY-III",
            "alpha_2": country.pk,
        }

        for language in settings.LANGUAGES:
            language_code = language[0]
            language_name = language[1]

            translation_payload = {
                "name": f"Translation for {language_name}",
            }

            payload["translations"][language_code] = translation_payload

        response = self.client.post(
            "/api/v1/region/", json.dumps(payload), content_type="application/json"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_invalid(self):
        payload = {
            "alpha": "INVALID_ALPHA",
            "alpha_2": "INVALID",
        }
        response = self.client.post(
            "/api/v1/region/", json.dumps(payload), content_type="application/json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_retrieve_valid(self):
        response = self.client.get(f"/api/v1/region/{self.region.pk}/")
        region = Region.objects.get(pk=self.region.pk)
        serializer = RegionSerializer(region)
        self.assertEqual(response.data, serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_retrieve_invalid(self):
        invalid_region = "invalid"
        response = self.client.get(f"/api/v1/region/{invalid_region}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_valid(self):
        country = Country.objects.create(
            alpha_2="CY",
            alpha_3="CYP",
            iso_cc=196,
            phone_code=357,
        )
        payload = {
            "translations": {},
            "alpha": "GR-I",
            "alpha_2": country.pk,
        }

        for language in settings.LANGUAGES:
            language_code = language[0]
            language_name = language[1]

            translation_payload = {
                "name": f"Translation for {language_name}",
            }

            payload["translations"][language_code] = translation_payload

        response = self.client.put(
            f"/api/v1/region/{self.region.pk}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_invalid(self):
        payload = {
            "alpha": "INVALID_ALPHA",
            "alpha_2": "INVALID",
        }
        response = self.client.put(
            f"/api/v1/region/{self.region.pk}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_partial_update_valid(self):
        payload = {}
        response = self.client.patch(
            f"/api/v1/region/{self.region.pk}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_partial_update_invalid(self):
        payload = {
            "alpha": "INVALID_ALPHA",
            "alpha_2": "INVALID",
        }
        response = self.client.patch(
            f"/api/v1/region/{self.region.pk}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_destroy_valid(self):
        response = self.client.delete(f"/api/v1/region/{self.region.pk}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_destroy_invalid(self):
        invalid_region = "invalid"
        response = self.client.delete(f"/api/v1/region/{invalid_region}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
