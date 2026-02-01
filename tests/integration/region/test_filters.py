import uuid

from django.conf import settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from country.factories import CountryFactory
from region.factories import RegionFactory
from core.utils.testing import TestURLFixerMixin

languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE


class RegionFilterTestCase(TestURLFixerMixin, APITestCase):
    @classmethod
    def setUpTestData(cls):
        # Use unique alpha codes to avoid conflicts with parallel tests
        cls.test_id = uuid.uuid4().hex[:4].upper()

        cls.country_gr = CountryFactory(
            alpha_2=f"G{cls.test_id[:1]}",
            alpha_3=f"GR{cls.test_id[:1]}",
            iso_cc=300 + int(cls.test_id[:2], 16) % 100,
            phone_code=30,
        )
        cls.country_us = CountryFactory(
            alpha_2=f"U{cls.test_id[1:2]}",
            alpha_3=f"US{cls.test_id[1:2]}",
            iso_cc=840 + int(cls.test_id[:2], 16) % 100,
            phone_code=1,
        )
        cls.country_de = CountryFactory(
            alpha_2=f"D{cls.test_id[2:3]}",
            alpha_3=f"DE{cls.test_id[2:3]}",
            iso_cc=276 + int(cls.test_id[:2], 16) % 100,
            phone_code=49,
        )

        for country in [cls.country_gr, cls.country_us, cls.country_de]:
            for lang in languages:
                country.set_current_language(lang)
                if country == cls.country_gr:
                    country.name = (
                        f"Greece-{cls.test_id}"
                        if lang == "en"
                        else f"Ελλάδα-{cls.test_id}"
                    )
                elif country == cls.country_us:
                    country.name = (
                        f"United States-{cls.test_id}"
                        if lang == "en"
                        else f"Ηνωμένες Πολιτείες-{cls.test_id}"
                    )
                elif country == cls.country_de:
                    country.name = (
                        f"Germany-{cls.test_id}"
                        if lang == "en"
                        else f"Γερμανία-{cls.test_id}"
                    )
                country.save()

        cls.region_attica = RegionFactory(
            alpha=f"{cls.country_gr.alpha_2}-A",
            country=cls.country_gr,
            sort_order=1,
        )
        cls.region_crete = RegionFactory(
            alpha=f"{cls.country_gr.alpha_2}-M",
            country=cls.country_gr,
            sort_order=2,
        )
        cls.region_california = RegionFactory(
            alpha=f"{cls.country_us.alpha_2}-CA",
            country=cls.country_us,
            sort_order=1,
        )
        cls.region_texas = RegionFactory(
            alpha=f"{cls.country_us.alpha_2}-TX",
            country=cls.country_us,
            sort_order=2,
        )
        cls.region_bavaria = RegionFactory(
            alpha=f"{cls.country_de.alpha_2}-BY",
            country=cls.country_de,
            sort_order=1,
        )

        region_translations = {
            cls.region_attica: {"en": "Attica", "el": "Αττική"},
            cls.region_crete: {"en": "Crete", "el": "Κρήτη"},
            cls.region_california: {"en": "California", "el": "Καλιφόρνια"},
            cls.region_texas: {"en": "Texas", "el": "Τέξας"},
            cls.region_bavaria: {"en": "Bavaria", "el": "Βαυαρία"},
        }

        for region, translations in region_translations.items():
            for lang in languages:
                region.set_current_language(lang)
                region.name = translations.get(lang, translations["en"])
                region.save()

    def get_region_list_url(self):
        return reverse("region-list")

    def test_filter_by_alpha_partial_match(self):
        response = self.client.get(
            self.get_region_list_url(), {"alpha": self.country_gr.alpha_2}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertEqual(len(response.data["results"]), 2)
        alpha_codes = [item["alpha"] for item in response.data["results"]]
        self.assertIn(self.region_attica.alpha, alpha_codes)
        self.assertIn(self.region_crete.alpha, alpha_codes)

    def test_filter_by_alpha_exact_match(self):
        response = self.client.get(
            self.get_region_list_url(),
            {"alpha_exact": self.region_attica.alpha},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(
            response.data["results"][0]["alpha"], self.region_attica.alpha
        )

    def test_filter_by_alpha_exact_no_match(self):
        response = self.client.get(
            self.get_region_list_url(), {"alpha_exact": "XX-YY"}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertEqual(len(response.data["results"]), 0)

    def test_filter_by_country_alpha_2(self):
        response = self.client.get(
            self.get_region_list_url(), {"country": self.country_us.alpha_2}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertEqual(len(response.data["results"]), 2)
        for item in response.data["results"]:
            self.assertEqual(item["country"], self.country_us.alpha_2)

    def test_filter_by_country_name_partial_match(self):
        response = self.client.get(
            self.get_region_list_url(),
            {"country_name": f"Greece-{self.test_id}"},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertEqual(len(response.data["results"]), 2)
        for item in response.data["results"]:
            self.assertEqual(item["country"], self.country_gr.alpha_2)

    def test_filter_by_country_name_case_insensitive(self):
        response = self.client.get(
            self.get_region_list_url(),
            {"country_name": f"greece-{self.test_id}"},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertEqual(len(response.data["results"]), 2)
        for item in response.data["results"]:
            self.assertEqual(item["country"], self.country_gr.alpha_2)

    def test_filter_by_region_name_partial_match(self):
        response = self.client.get(
            self.get_region_list_url(), {"name": "Attica"}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertGreaterEqual(len(response.data["results"]), 1)
        alpha_codes = [item["alpha"] for item in response.data["results"]]
        self.assertIn(self.region_attica.alpha, alpha_codes)

    def test_filter_by_region_name_case_insensitive(self):
        response = self.client.get(
            self.get_region_list_url(), {"name": "attica"}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertGreaterEqual(len(response.data["results"]), 1)
        alpha_codes = [item["alpha"] for item in response.data["results"]]
        self.assertIn(self.region_attica.alpha, alpha_codes)

    def test_filter_by_uuid(self):
        response = self.client.get(
            self.get_region_list_url(), {"uuid": str(self.region_attica.uuid)}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(
            response.data["results"][0]["uuid"], str(self.region_attica.uuid)
        )

    def test_filter_by_invalid_uuid(self):
        response = self.client.get(
            self.get_region_list_url(), {"uuid": "invalid-uuid"}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_filter_by_created_at_after(self):
        new_region = RegionFactory(
            alpha=f"T{self.test_id[:3]}1",
            country=self.country_gr,
        )

        response = self.client.get(
            self.get_region_list_url(),
            {"created_at_after": new_region.created_at.isoformat()},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        uuids = [item["uuid"] for item in response.data["results"]]
        self.assertIn(str(new_region.uuid), uuids)

    def test_filter_by_created_at_before(self):
        from django.utils import timezone

        future_date = timezone.now() + timezone.timedelta(days=1)

        response = self.client.get(
            self.get_region_list_url(),
            {"created_at_before": future_date.isoformat()},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertGreater(len(response.data["results"]), 0)

    def test_filter_by_updated_at_after(self):
        self.region_attica.set_current_language("en")
        self.region_attica.name = "Updated Attica"
        self.region_attica.save()

        response = self.client.get(
            self.get_region_list_url(),
            {"updated_at_after": self.region_attica.updated_at.isoformat()},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        uuids = [item["uuid"] for item in response.data["results"]]
        self.assertIn(str(self.region_attica.uuid), uuids)

    def test_ordering_by_sort_order(self):
        response = self.client.get(
            self.get_region_list_url(), {"ordering": "sort_order"}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        sort_orders = [item["sort_order"] for item in response.data["results"]]
        self.assertEqual(sort_orders, sorted(sort_orders))

    def test_ordering_by_sort_order_desc(self):
        response = self.client.get(
            self.get_region_list_url(), {"ordering": "-sort_order"}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        sort_orders = [item["sort_order"] for item in response.data["results"]]
        self.assertEqual(sort_orders, sorted(sort_orders, reverse=True))

    def test_ordering_by_alpha(self):
        response = self.client.get(
            self.get_region_list_url(), {"ordering": "alpha"}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        alpha_codes = [item["alpha"] for item in response.data["results"]]
        self.assertEqual(alpha_codes, sorted(alpha_codes))

    def test_ordering_by_created_at(self):
        response = self.client.get(
            self.get_region_list_url(), {"ordering": "created_at"}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertGreater(len(response.data["results"]), 0)

    def test_combined_filters(self):
        response = self.client.get(
            self.get_region_list_url(),
            {
                "country": self.country_gr.alpha_2,
                "alpha": self.region_attica.alpha,
                "ordering": "sort_order",
            },
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertEqual(len(response.data["results"]), 1)
        result = response.data["results"][0]
        self.assertEqual(result["alpha"], self.region_attica.alpha)
        self.assertEqual(result["country"], self.country_gr.alpha_2)

    def test_search_functionality(self):
        response = self.client.get(
            self.get_region_list_url(), {"search": self.region_attica.alpha}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        found = any(
            item["alpha"] == self.region_attica.alpha
            for item in response.data["results"]
        )
        self.assertTrue(found)

        response = self.client.get(
            self.get_region_list_url(), {"search": "California"}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        found = any(
            item["alpha"] == self.region_california.alpha
            for item in response.data["results"]
        )
        self.assertTrue(found)

        response = self.client.get(
            self.get_region_list_url(), {"search": self.country_de.alpha_2}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        found = any(
            item["country"] == self.country_de.alpha_2
            for item in response.data["results"]
        )
        self.assertTrue(found)

    def test_camel_case_filter_conversion(self):
        response = self.client.get(
            self.get_region_list_url(),
            {"createdAtAfter": "2020-01-01T00:00:00Z"},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        response = self.client.get(
            self.get_region_list_url(),
            {"countryName": f"Greece-{self.test_id}"},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertEqual(len(response.data["results"]), 2)
        for item in response.data["results"]:
            self.assertEqual(item["country"], self.country_gr.alpha_2)

    def test_empty_filter_values(self):
        response = self.client.get(
            self.get_region_list_url(), {"alpha": "", "country": "", "name": ""}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertEqual(len(response.data["results"]), 5)

    def test_nonexistent_filter_values(self):
        response = self.client.get(
            self.get_region_list_url(),
            {
                "alpha": "NONEXISTENT",
                "country": "XX",
                "name": "NonexistentRegion",
            },
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertEqual(len(response.data["results"]), 0)

    def test_pagination_with_filters(self):
        for i in range(10):
            RegionFactory(
                alpha=f"T{self.test_id[:2]}{i}",
                country=self.country_gr,
            )

        response = self.client.get(
            self.get_region_list_url(),
            {"country": self.country_gr.alpha_2, "page_size": 5},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertIn("count", response.data)
        self.assertGreaterEqual(response.data["count"], 12)
        self.assertLessEqual(len(response.data["results"]), 5)
