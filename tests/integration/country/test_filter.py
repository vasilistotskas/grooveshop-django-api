from datetime import timedelta

from django.db import connection
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from country.models import Country
from shipping.models import ShippingRate


class CountryFilterTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        # The assertions cover the whole list, so the ISO seed goes —
        # inside the class transaction, which puts it back afterwards.
        ShippingRate.objects.all().delete()
        Country.objects.all().delete()

        cls.now = timezone.now()

        cls.usa = Country.objects.create(
            alpha_2="US", alpha_3="USA", iso_cc=840, phone_code=1, sort_order=1
        )
        cls.usa.created_at = cls.now - timedelta(days=365)
        cls.usa.save()
        cls.usa.set_current_language("en")
        cls.usa.name = "United States"
        cls.usa.save()
        cls.usa.image_flag = "uploads/country/us.png"
        cls.usa.save()

        cls.uk = Country.objects.create(
            alpha_2="GB", alpha_3="GBR", iso_cc=826, phone_code=44, sort_order=2
        )
        cls.uk.created_at = cls.now - timedelta(days=300)
        cls.uk.save()
        cls.uk.set_current_language("en")
        cls.uk.name = "United Kingdom"
        cls.uk.save()
        cls.uk.image_flag = "uploads/country/gb.png"
        cls.uk.save()

        cls.germany = Country.objects.create(
            alpha_2="DE", alpha_3="DEU", iso_cc=276, phone_code=49, sort_order=3
        )
        cls.germany.created_at = cls.now - timedelta(days=200)
        cls.germany.save()
        cls.germany.set_current_language("en")
        cls.germany.name = "Germany"
        cls.germany.save()
        cls.germany.image_flag = "uploads/country/de.png"
        cls.germany.save()

        cls.france = Country.objects.create(
            alpha_2="FR", alpha_3="FRA", iso_cc=250, phone_code=33, sort_order=4
        )
        cls.france.created_at = cls.now - timedelta(days=150)
        cls.france.save()
        cls.france.set_current_language("en")
        cls.france.name = "France"
        cls.france.save()

        cls.japan = Country.objects.create(
            alpha_2="JP", alpha_3="JPN", iso_cc=392, phone_code=81, sort_order=5
        )
        cls.japan.created_at = cls.now - timedelta(days=35)
        cls.japan.save()
        cls.japan.set_current_language("en")
        cls.japan.name = "Japan"
        cls.japan.save()
        cls.japan.image_flag = "uploads/country/jp.png"
        cls.japan.save()

        cls.brazil = Country.objects.create(
            alpha_2="BR",
            alpha_3="BRA",
            iso_cc=None,
            phone_code=55,
            sort_order=6,
        )
        cls.brazil.created_at = cls.now - timedelta(days=50)
        cls.brazil.save()
        cls.brazil.set_current_language("en")
        cls.brazil.name = "Brazil"
        cls.brazil.save()
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE country_country SET updated_at = %s WHERE alpha_2 = %s",
                [cls.now - timedelta(days=50), "BR"],
            )

        cls.canada = Country.objects.create(
            alpha_2="CA",
            alpha_3="CAN",
            iso_cc=124,
            phone_code=None,
            sort_order=7,
        )
        cls.canada.created_at = cls.now - timedelta(days=30)
        cls.canada.save()
        cls.canada.set_current_language("en")
        cls.canada.name = "Canada"
        cls.canada.save()
        cls.canada.image_flag = "uploads/country/ca.png"
        cls.canada.save()

        cls.unknown = Country.objects.create(
            alpha_2="XX",
            alpha_3="XXX",
            iso_cc=999,
            phone_code=999,
            sort_order=8,
        )
        cls.unknown.created_at = cls.now - timedelta(days=10)
        cls.unknown.save()

        cls.australia = Country.objects.create(
            alpha_2="AU", alpha_3="AUS", iso_cc=36, phone_code=61, sort_order=9
        )
        cls.australia.created_at = cls.now - timedelta(days=25)
        cls.australia.save()
        cls.australia.set_current_language("en")
        cls.australia.name = "Australia"
        cls.australia.save()
        cls.australia.image_flag = "uploads/country/au.png"
        cls.australia.save()

    def test_timestamp_filters(self):
        url = reverse("country-list")

        created_after = self.now - timedelta(days=40)
        response = self.client.get(
            url, {"created_at__gte": created_after.isoformat()}
        )
        self.assertEqual(response.status_code, 200)
        result_codes = [r["alpha_2"] for r in response.data["results"]]
        self.assertIn("JP", result_codes)
        self.assertIn("AU", result_codes)
        self.assertIn("CA", result_codes)
        self.assertIn("XX", result_codes)
        self.assertNotIn("BR", result_codes)

        updated_before = self.now - timedelta(days=40)
        response = self.client.get(
            url, {"updated_at__lte": updated_before.isoformat()}
        )
        self.assertEqual(response.status_code, 200)
        result_codes = [r["alpha_2"] for r in response.data["results"]]
        self.assertNotIn("JP", result_codes)
        self.assertNotIn("AU", result_codes)
        self.assertNotIn("CA", result_codes)
        self.assertNotIn("XX", result_codes)
        self.assertIn("BR", result_codes)

    def test_uuid_and_sort_order_filters(self):
        url = reverse("country-list")

        response = self.client.get(url, {"uuid": str(self.germany.uuid)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["alpha_2"], "DE")

        response = self.client.get(url, {"sort_order": 3})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["alpha_2"], "DE")

        response = self.client.get(url, {"sort_order_min": 5})
        self.assertEqual(response.status_code, 200)
        result_codes = [r["alpha_2"] for r in response.data["results"]]
        self.assertIn("JP", result_codes)
        self.assertIn("BR", result_codes)
        self.assertIn("CA", result_codes)
        self.assertIn("XX", result_codes)
        self.assertIn("AU", result_codes)

    def test_code_filters(self):
        url = reverse("country-list")

        response = self.client.get(url, {"alpha_2": "US"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["alpha_2"], "US")

        response = self.client.get(url, {"alpha_2__icontains": "U"})
        self.assertEqual(response.status_code, 200)
        result_codes = [r["alpha_2"] for r in response.data["results"]]
        self.assertIn("US", result_codes)
        self.assertIn("AU", result_codes)

        response = self.client.get(url, {"alpha_3": "GBR"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["alpha_3"], "GBR")

        response = self.client.get(url, {"alpha_3__icontains": "RA"})
        self.assertEqual(response.status_code, 200)
        result_codes = [r["alpha_2"] for r in response.data["results"]]
        self.assertIn("FR", result_codes)
        self.assertIn("BR", result_codes)

    def test_iso_and_phone_code_filters(self):
        url = reverse("country-list")

        response = self.client.get(url, {"iso_cc": 840})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["alpha_2"], "US")

        response = self.client.get(url, {"iso_cc_min": 200, "iso_cc_max": 300})
        self.assertEqual(response.status_code, 200)
        result_codes = [r["alpha_2"] for r in response.data["results"]]
        self.assertIn("DE", result_codes)
        self.assertIn("FR", result_codes)

        response = self.client.get(url, {"phone_code": 44})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["alpha_2"], "GB")

        response = self.client.get(
            url, {"phone_code_min": 40, "phone_code_max": 60}
        )
        self.assertEqual(response.status_code, 200)
        result_codes = [r["alpha_2"] for r in response.data["results"]]
        self.assertIn("GB", result_codes)
        self.assertIn("DE", result_codes)
        self.assertIn("BR", result_codes)

    def test_name_filters(self):
        url = reverse("country-list")

        response = self.client.get(url, {"name": "united"})
        self.assertEqual(response.status_code, 200)
        result_codes = [r["alpha_2"] for r in response.data["results"]]
        self.assertIn("US", result_codes)
        self.assertIn("GB", result_codes)

        response = self.client.get(url, {"name__exact": "Japan"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["alpha_2"], "JP")

        response = self.client.get(url, {"name__startswith": "Ger"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["alpha_2"], "DE")

    def test_special_filters(self):
        url = reverse("country-list")

        response = self.client.get(url, {"multiple_codes": "US,GBR,JP"})
        self.assertEqual(response.status_code, 200)
        result_codes = [r["alpha_2"] for r in response.data["results"]]
        self.assertIn("US", result_codes)
        self.assertIn("GB", result_codes)
        self.assertIn("JP", result_codes)
        self.assertEqual(len(result_codes), 3)

        response = self.client.get(url, {"is_eu": "true"})
        self.assertEqual(response.status_code, 200)
        result_codes = [r["alpha_2"] for r in response.data["results"]]
        self.assertIn("DE", result_codes)
        self.assertIn("FR", result_codes)
        self.assertNotIn("US", result_codes)
        self.assertNotIn("GB", result_codes)

        response = self.client.get(url, {"has_name": "true"})
        self.assertEqual(response.status_code, 200)
        result_codes = [r["alpha_2"] for r in response.data["results"]]
        self.assertIn("US", result_codes)
        self.assertIn("GB", result_codes)
        self.assertIn("DE", result_codes)
        self.assertIn("FR", result_codes)
        self.assertIn("JP", result_codes)
        self.assertIn("BR", result_codes)
        self.assertIn("CA", result_codes)
        self.assertIn("AU", result_codes)
        self.assertNotIn("XX", result_codes)

    def test_camel_case_filters(self):
        url = reverse("country-list")

        created_after = self.now - timedelta(days=60)
        response = self.client.get(
            url,
            {
                "createdAfter": created_after.isoformat(),
                "hasIsoCC": "true",
                "hasFlagImage": "true",
            },
        )
        self.assertEqual(response.status_code, 200)

        result_codes = [r["alpha_2"] for r in response.data["results"]]
        self.assertIn("JP", result_codes)
        self.assertIn("CA", result_codes)
        self.assertIn("AU", result_codes)
        self.assertNotIn("BR", result_codes)

        response = self.client.get(url, {"phoneCode": 1})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["alpha_2"], "US")

    def test_complex_filter_combinations(self):
        url = reverse("country-list")

        response = self.client.get(
            url, {"is_eu": "true", "has_all_data": "true", "sort_order_max": 5}
        )
        self.assertEqual(response.status_code, 200)
        result_codes = [r["alpha_2"] for r in response.data["results"]]
        self.assertIn("DE", result_codes)
        self.assertNotIn("FR", result_codes)

    def test_filter_with_ordering(self):
        url = reverse("country-list")

        response = self.client.get(
            url, {"has_name": "true", "ordering": "sort_order"}
        )
        self.assertEqual(response.status_code, 200)

        results = response.data["results"]
        self.assertEqual(results[0]["alpha_2"], "US")
        self.assertEqual(results[1]["alpha_2"], "GB")
        self.assertEqual(results[2]["alpha_2"], "DE")
        self.assertEqual(results[3]["alpha_2"], "FR")
