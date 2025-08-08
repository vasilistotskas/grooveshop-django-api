from datetime import timedelta
from django.utils import timezone
from django.urls import reverse
from django.test import TransactionTestCase
from rest_framework.test import APIClient

from country.models import Country


class CountryFilterTest(TransactionTestCase):
    def setUp(self):
        self.client = APIClient()
        Country.objects.all().delete()

        self.now = timezone.now()

        self.usa = Country.objects.create(
            alpha_2="US", alpha_3="USA", iso_cc=840, phone_code=1, sort_order=1
        )
        self.usa.created_at = self.now - timedelta(days=365)
        self.usa.save()
        self.usa.set_current_language("en")
        self.usa.name = "United States"
        self.usa.save()
        self.usa.image_flag = "uploads/country/us.png"
        self.usa.save()

        self.uk = Country.objects.create(
            alpha_2="GB", alpha_3="GBR", iso_cc=826, phone_code=44, sort_order=2
        )
        self.uk.created_at = self.now - timedelta(days=300)
        self.uk.save()
        self.uk.set_current_language("en")
        self.uk.name = "United Kingdom"
        self.uk.save()
        self.uk.image_flag = "uploads/country/gb.png"
        self.uk.save()

        self.germany = Country.objects.create(
            alpha_2="DE", alpha_3="DEU", iso_cc=276, phone_code=49, sort_order=3
        )
        self.germany.created_at = self.now - timedelta(days=200)
        self.germany.save()
        self.germany.set_current_language("en")
        self.germany.name = "Germany"
        self.germany.save()
        self.germany.image_flag = "uploads/country/de.png"
        self.germany.save()

        self.france = Country.objects.create(
            alpha_2="FR", alpha_3="FRA", iso_cc=250, phone_code=33, sort_order=4
        )
        self.france.created_at = self.now - timedelta(days=150)
        self.france.save()
        self.france.set_current_language("en")
        self.france.name = "France"
        self.france.save()

        self.japan = Country.objects.create(
            alpha_2="JP", alpha_3="JPN", iso_cc=392, phone_code=81, sort_order=5
        )
        self.japan.created_at = self.now - timedelta(days=35)
        self.japan.save()
        self.japan.set_current_language("en")
        self.japan.name = "Japan"
        self.japan.save()
        self.japan.image_flag = "uploads/country/jp.png"
        self.japan.save()

        self.brazil = Country.objects.create(
            alpha_2="BR",
            alpha_3="BRA",
            iso_cc=None,
            phone_code=55,
            sort_order=6,
        )
        self.brazil.created_at = self.now - timedelta(days=50)
        self.brazil.save()
        self.brazil.set_current_language("en")
        self.brazil.name = "Brazil"
        self.brazil.save()
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE country_country SET updated_at = %s WHERE alpha_2 = %s",
                [self.now - timedelta(days=50), "BR"],
            )

        self.canada = Country.objects.create(
            alpha_2="CA",
            alpha_3="CAN",
            iso_cc=124,
            phone_code=None,
            sort_order=7,
        )
        self.canada.created_at = self.now - timedelta(days=30)
        self.canada.save()
        self.canada.set_current_language("en")
        self.canada.name = "Canada"
        self.canada.save()
        self.canada.image_flag = "uploads/country/ca.png"
        self.canada.save()

        self.unknown = Country.objects.create(
            alpha_2="XX",
            alpha_3="XXX",
            iso_cc=999,
            phone_code=999,
            sort_order=8,
        )
        self.unknown.created_at = self.now - timedelta(days=10)
        self.unknown.save()

        self.australia = Country.objects.create(
            alpha_2="AU", alpha_3="AUS", iso_cc=36, phone_code=61, sort_order=9
        )
        self.australia.created_at = self.now - timedelta(days=25)
        self.australia.save()
        self.australia.set_current_language("en")
        self.australia.name = "Australia"
        self.australia.save()
        self.australia.image_flag = "uploads/country/au.png"
        self.australia.save()

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

    def tearDown(self):
        Country.objects.all().delete()
