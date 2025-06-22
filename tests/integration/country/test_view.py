from django.conf import settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from core.utils.testing import TestURLFixerMixin
from country.factories import CountryFactory
from country.models import Country
from country.serializers import (
    CountryDetailSerializer,
    CountrySerializer,
)

languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE


class CountryViewSetTestCase(TestURLFixerMixin, APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.country = CountryFactory(
            alpha_2="GR",
            alpha_3="GRC",
            iso_cc=300,
            phone_code=30,
            num_regions=0,
        )
        cls.country2 = CountryFactory(
            alpha_2="US",
            alpha_3="USA",
            iso_cc=840,
            phone_code=1,
            num_regions=0,
        )
        cls.country3 = CountryFactory(
            alpha_2="FR",
            alpha_3="FRA",
            iso_cc=250,
            phone_code=33,
            num_regions=0,
        )

    def setUp(self):
        self.list_url = reverse("country-list")
        self.detail_url = reverse("country-detail", args=[self.country.pk])

    def test_list_uses_correct_serializer(self):
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertIn("results", response.data)
        self.assertIn("count", response.data)

        if response.data["results"]:
            serializer = CountrySerializer(instance=self.country)
            expected_fields = set(serializer.data.keys())
            actual_fields = set(response.data["results"][0].keys())
            self.assertEqual(expected_fields, actual_fields)

    def test_retrieve_uses_correct_serializer(self):
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        serializer = CountryDetailSerializer(instance=self.country)
        expected_fields = set(serializer.data.keys())
        actual_fields = set(response.data.keys())
        self.assertEqual(expected_fields, actual_fields)

    def test_create_uses_correct_serializer(self):
        payload = {
            "alpha_2": "CY",
            "alpha_3": "CYP",
            "iso_cc": 196,
            "phone_code": 997,
            "translations": {default_language: {"name": "Cyprus"}},
        }

        response = self.client.post(self.list_url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        created_country = Country.objects.get(alpha_2="CY")
        serializer = CountryDetailSerializer(instance=created_country)
        expected_fields = set(serializer.data.keys())
        actual_fields = set(response.data.keys())
        self.assertEqual(expected_fields, actual_fields)

    def test_update_uses_correct_serializer(self):
        payload = {
            "alpha_2": "GR",
            "alpha_3": "GRC",
            "iso_cc": 301,
            "phone_code": 30,
            "translations": {default_language: {"name": "Updated Greece"}},
        }

        response = self.client.put(self.detail_url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        serializer = CountryDetailSerializer(instance=self.country)
        expected_fields = set(serializer.data.keys())
        actual_fields = set(response.data.keys())
        self.assertEqual(expected_fields, actual_fields)

    def test_list_countries(self):
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertIn("results", response.data)
        self.assertIn("count", response.data)

        self.assertGreaterEqual(response.data["count"], 3)

    def test_retrieve_country(self):
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertEqual(response.data["alpha_2"], self.country.alpha_2)
        self.assertEqual(response.data["alpha_3"], self.country.alpha_3)
        self.assertEqual(response.data["iso_cc"], self.country.iso_cc)
        self.assertIn("translations", response.data)

    def test_create_country(self):
        initial_count = Country.objects.count()
        payload = {
            "alpha_2": "CA",
            "alpha_3": "CAN",
            "iso_cc": 124,
            "phone_code": 999,
            "translations": {default_language: {"name": "Canada"}},
        }

        response = self.client.post(self.list_url, payload, format="json")
        if response.status_code != status.HTTP_201_CREATED:
            print(f"Error response: {response.data}")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Country.objects.count(), initial_count + 1)

        country = Country.objects.get(alpha_2="CA")
        self.assertEqual(country.alpha_3, "CAN")
        self.assertEqual(country.iso_cc, 124)

    def test_update_country(self):
        payload = {
            "alpha_2": "GR",
            "alpha_3": "GRC",
            "iso_cc": 302,
            "phone_code": 30,
            "translations": {default_language: {"name": "Hellenic Republic"}},
        }

        response = self.client.put(self.detail_url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.country.refresh_from_db()
        self.assertEqual(self.country.iso_cc, 302)

    def test_partial_update_country(self):
        payload = {"iso_cc": 303}

        response = self.client.patch(self.detail_url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.country.refresh_from_db()
        self.assertEqual(self.country.iso_cc, 303)

    def test_delete_country(self):
        country_id = self.country.pk
        response = self.client.delete(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Country.objects.filter(pk=country_id).exists())

    def test_filter_by_alpha_2(self):
        response = self.client.get(self.list_url, {"alpha_2": "GR"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["alpha_2"], "GR")

    def test_filter_by_alpha_3(self):
        response = self.client.get(self.list_url, {"alpha_3": "USA"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["alpha_3"], "USA")

    def test_filter_by_iso_cc(self):
        response = self.client.get(self.list_url, {"iso_cc": 840})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["iso_cc"], 840)

    def test_filter_by_phone_code(self):
        response = self.client.get(self.list_url, {"phone_code": 33})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["phone_code"], 33)

    def test_filter_by_iso_cc_range(self):
        response = self.client.get(self.list_url, {"iso_cc_min": 300})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        iso_codes = [
            item["iso_cc"]
            for item in response.data["results"]
            if item["iso_cc"]
        ]
        self.assertTrue(all(code >= 300 for code in iso_codes))

        response = self.client.get(self.list_url, {"iso_cc_max": 500})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        iso_codes = [
            item["iso_cc"]
            for item in response.data["results"]
            if item["iso_cc"]
        ]
        self.assertTrue(all(code <= 500 for code in iso_codes))

    def test_filter_has_iso_cc(self):
        response = self.client.get(self.list_url, {"has_iso_cc": "true"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        for item in response.data["results"]:
            self.assertIsNotNone(item["iso_cc"])

    def test_filter_has_phone_code(self):
        response = self.client.get(self.list_url, {"has_phone_code": "true"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        for item in response.data["results"]:
            self.assertIsNotNone(item["phone_code"])

    def test_ordering_by_alpha_2(self):
        response = self.client.get(self.list_url, {"ordering": "alpha_2"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        alpha_2_codes = [item["alpha_2"] for item in response.data["results"]]
        self.assertEqual(alpha_2_codes, sorted(alpha_2_codes))

    def test_ordering_by_iso_cc_desc(self):
        response = self.client.get(self.list_url, {"ordering": "-iso_cc"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        iso_codes = [
            item["iso_cc"]
            for item in response.data["results"]
            if item["iso_cc"]
        ]
        self.assertEqual(iso_codes, sorted(iso_codes, reverse=True))

    def test_ordering_by_created_at(self):
        response = self.client.get(self.list_url, {"ordering": "created_at"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertGreater(len(response.data["results"]), 0)

    def test_search_by_alpha_2(self):
        response = self.client.get(self.list_url, {"search": "GR"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        found = any(
            item["alpha_2"] == "GR" for item in response.data["results"]
        )
        self.assertTrue(found)

    def test_search_by_alpha_3(self):
        response = self.client.get(self.list_url, {"search": "USA"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        found = any(
            item["alpha_3"] == "USA" for item in response.data["results"]
        )
        self.assertTrue(found)

    def test_search_by_phone_code(self):
        response = self.client.get(self.list_url, {"search": "33"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertGreater(len(response.data["results"]), 0)

    def test_create_with_invalid_alpha_2(self):
        payload = {
            "alpha_2": "invalid_alpha_2",
            "alpha_3": "CYP",
            "iso_cc": 196,
            "phone_code": 357,
            "translations": {default_language: {"name": "Cyprus"}},
        }

        response = self.client.post(self.list_url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_with_invalid_alpha_3(self):
        payload = {
            "alpha_2": "CY",
            "alpha_3": "INVALID",
            "iso_cc": 196,
            "phone_code": 357,
            "translations": {default_language: {"name": "Cyprus"}},
        }

        response = self.client.post(self.list_url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_with_duplicate_alpha_2(self):
        payload = {
            "alpha_2": "GR",
            "alpha_3": "CYP",
            "iso_cc": 196,
            "phone_code": 357,
            "translations": {default_language: {"name": "Cyprus"}},
        }

        response = self.client.post(self.list_url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_retrieve_nonexistent_country(self):
        nonexistent_url = reverse("country-detail", args=["nonexistent"])
        response = self.client.get(nonexistent_url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_nonexistent_country(self):
        nonexistent_url = reverse("country-detail", args=["nonexistent"])
        payload = {"iso_cc": 999}

        response = self.client.patch(nonexistent_url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_alpha_codes_case_normalization(self):
        payload_lowercase = {
            "alpha_2": "cy",
            "alpha_3": "cyp",
            "iso_cc": 196,
            "phone_code": 998,
            "translations": {default_language: {"name": "Cyprus"}},
        }

        response = self.client.post(
            self.list_url, payload_lowercase, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("alpha_2", response.data)
        self.assertIn("alpha_3", response.data)

        payload_uppercase = {
            "alpha_2": "CY",
            "alpha_3": "CYP",
            "iso_cc": 196,
            "phone_code": 998,
            "translations": {default_language: {"name": "Cyprus"}},
        }

        response = self.client.post(
            self.list_url, payload_uppercase, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        self.assertEqual(response.data["alpha_2"], "CY")
        self.assertEqual(response.data["alpha_3"], "CYP")

    def test_response_includes_main_image_path(self):
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("main_image_path", response.data)

    def test_consistency_with_manual_serializer_instantiation(self):
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        manual_serializer = CountryDetailSerializer(instance=self.country)

        for field in ["alpha_2", "alpha_3", "iso_cc", "phone_code"]:
            self.assertEqual(
                response.data[field], manual_serializer.data[field]
            )

    def test_validation_errors_consistent(self):
        payload = {
            "alpha_2": "",
            "alpha_3": "",
        }

        response = self.client.post(self.list_url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        self.assertIn("alpha_2", response.data)
        self.assertIn("alpha_3", response.data)
