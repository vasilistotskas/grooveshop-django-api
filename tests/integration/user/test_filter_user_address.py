from datetime import timedelta

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from core.enum import FloorChoicesEnum, LocationChoicesEnum
from country.factories import CountryFactory
from region.factories import RegionFactory
from user.factories.account import UserAccountFactory
from user.factories.address import UserAddressFactory

User = get_user_model()


class UserAddressFilterTest(APITestCase):
    @classmethod
    def setUpTestData(cls):

        cls.user = UserAccountFactory(num_addresses=0)
        cls.other_user = UserAccountFactory(num_addresses=0)

        cls.usa = CountryFactory(alpha_2="US", alpha_3="USA")
        cls.canada = CountryFactory(alpha_2="CA", alpha_3="CAN")
        cls.greece = CountryFactory(alpha_2="GR", alpha_3="GRC")

        cls.ny_region = RegionFactory(country=cls.usa, alpha="NY")
        cls.ca_region = RegionFactory(country=cls.usa, alpha="CA")
        cls.on_region = RegionFactory(country=cls.canada, alpha="ON")
        cls.att_region = RegionFactory(country=cls.greece, alpha="ATT")

        cls.now = timezone.now()

        cls.home_address = UserAddressFactory(
            user=cls.user,
            title="Home Address",
            first_name="John",
            last_name="Doe",
            street="Main Street",
            street_number="123",
            city="New York",
            zipcode="10001",
            country=cls.usa,
            region=cls.ny_region,
            floor=FloorChoicesEnum.GROUND_FLOOR,
            location_type=LocationChoicesEnum.HOME,
            phone="+1234567890",
            notes="Primary residence",
            is_main=True,
        )
        cls.home_address.created_at = cls.now - timedelta(days=30)
        cls.home_address.save()

        cls.work_address = UserAddressFactory(
            user=cls.user,
            title="Work Office",
            first_name="John",
            last_name="Doe",
            street="Business Avenue",
            street_number="456",
            city="Los Angeles",
            zipcode="90210",
            country=cls.usa,
            region=cls.ca_region,
            floor=FloorChoicesEnum.FIRST_FLOOR,
            location_type=LocationChoicesEnum.OFFICE,
            phone="+1555123456",
            notes="",
            is_main=False,
        )
        cls.work_address.created_at = cls.now - timedelta(days=15)
        cls.work_address.save()

        cls.vacation_address = UserAddressFactory(
            user=cls.user,
            title="Vacation Home",
            first_name="Jane",
            last_name="Smith",
            street="Beach Road",
            street_number="789",
            city="Toronto",
            zipcode="M5V 3A8",
            country=cls.canada,
            region=cls.on_region,
            floor=FloorChoicesEnum.SECOND_FLOOR,
            location_type=LocationChoicesEnum.OTHER,
            phone="+1416555789",
            notes="Summer vacation spot",
            is_main=False,
        )
        cls.vacation_address.created_at = cls.now - timedelta(days=5)
        cls.vacation_address.save()

        cls.other_user_address = UserAddressFactory(
            user=cls.other_user,
            title="Other Home",
            first_name="Alice",
            last_name="Johnson",
            street="Oak Street",
            street_number="321",
            city="Athens",
            zipcode="10431",
            country=cls.greece,
            region=cls.att_region,
            floor=FloorChoicesEnum.THIRD_FLOOR,
            location_type=LocationChoicesEnum.HOME,
            phone="+302101234567",
            notes="Greek residence",
            is_main=True,
        )
        cls.other_user_address.created_at = cls.now - timedelta(days=10)
        cls.other_user_address.save()

    def setUp(self):
        self.client.force_authenticate(user=self.user)

    def test_timestamp_filters(self):
        url = reverse("user-address-list")

        created_after_date = self.now - timedelta(days=20)
        response = self.client.get(
            url,
            {"created_after": created_after_date.isoformat()},
        )
        self.assertEqual(response.status_code, 200)

        result_ids = [r["id"] for r in response.data["results"]]
        self.assertNotIn(self.home_address.id, result_ids)
        self.assertIn(self.work_address.id, result_ids)
        self.assertIn(self.vacation_address.id, result_ids)
        self.assertEqual(len(result_ids), 2)

        created_before_date = self.now - timedelta(days=20)
        response = self.client.get(
            url,
            {"created_before": created_before_date.isoformat()},
        )
        self.assertEqual(response.status_code, 200)

        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.home_address.id, result_ids)

    def test_uuid_filter(self):
        url = reverse("user-address-list")

        response = self.client.get(url, {"uuid": str(self.home_address.uuid)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(
            response.data["results"][0]["id"], self.home_address.id
        )

    def test_camel_case_filters(self):
        url = reverse("user-address-list")

        created_after_date = self.now - timedelta(days=20)
        response = self.client.get(
            url,
            {
                "createdAfter": created_after_date.isoformat(),
                "isMain": "false",
            },
        )
        self.assertEqual(response.status_code, 200)

        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.work_address.id, result_ids)
        self.assertIn(self.vacation_address.id, result_ids)

        response = self.client.get(
            url,
            {
                "firstName": "John",
                "locationType": LocationChoicesEnum.OFFICE,
            },
        )
        self.assertEqual(response.status_code, 200)

        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.work_address.id, result_ids)

    def test_location_filters(self):
        url = reverse("user-address-list")

        response = self.client.get(url, {"city": "New York"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.home_address.id, result_ids)

        response = self.client.get(url, {"city": "York"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.home_address.id, result_ids)

        response = self.client.get(url, {"zipcode": "90210"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.work_address.id, result_ids)

        response = self.client.get(url, {"street": "Main"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.home_address.id, result_ids)

    def test_country_and_region_filters(self):
        url = reverse("user-address-list")

        response = self.client.get(url, {"country": self.usa.alpha_2})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.home_address.id, result_ids)
        self.assertIn(self.work_address.id, result_ids)

        response = self.client.get(url, {"country_code": "CA"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.vacation_address.id, result_ids)

        response = self.client.get(url, {"region": self.ny_region.alpha})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.home_address.id, result_ids)

        response = self.client.get(url, {"region_code": "ON"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.vacation_address.id, result_ids)

    def test_name_filters(self):
        url = reverse("user-address-list")

        response = self.client.get(url, {"first_name": "John"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.home_address.id, result_ids)
        self.assertIn(self.work_address.id, result_ids)

        response = self.client.get(url, {"last_name": "Smith"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.vacation_address.id, result_ids)

        response = self.client.get(url, {"full_name": "Jane"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.vacation_address.id, result_ids)

        response = self.client.get(url, {"full_name": "John Doe"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.home_address.id, result_ids)
        self.assertIn(self.work_address.id, result_ids)

    def test_boolean_filters(self):
        url = reverse("user-address-list")

        response = self.client.get(url, {"is_main": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.home_address.id, result_ids)

        response = self.client.get(url, {"is_main": "false"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.work_address.id, result_ids)
        self.assertIn(self.vacation_address.id, result_ids)

        response = self.client.get(url, {"has_notes": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.home_address.id, result_ids)
        self.assertIn(self.vacation_address.id, result_ids)

        response = self.client.get(url, {"has_notes": "false"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.work_address.id, result_ids)

    def test_choice_filters(self):
        url = reverse("user-address-list")

        response = self.client.get(
            url, {"floor": FloorChoicesEnum.GROUND_FLOOR}
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.home_address.id, result_ids)

        response = self.client.get(
            url, {"location_type": LocationChoicesEnum.OFFICE}
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.work_address.id, result_ids)

        response = self.client.get(url, {"location_type_contains": "home"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.home_address.id, result_ids)

    def test_phone_filters(self):
        url = reverse("user-address-list")

        response = self.client.get(url, {"phone": "1234567890"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.home_address.id, result_ids)

        # Search for "555" which appears in work_address phone number
        response = self.client.get(url, {"phone": "555"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.work_address.id, result_ids)
        self.assertIn(self.vacation_address.id, result_ids)

    def test_complex_filter_combinations(self):
        url = reverse("user-address-list")

        created_after_date = self.now - timedelta(days=25)
        response = self.client.get(
            url,
            {
                "createdAfter": created_after_date.isoformat(),
                "countryCode": "US",
                "isMain": "false",
                "firstName": "John",
                "ordering": "-createdAt",
            },
        )

        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.work_address.id, result_ids)

        response = self.client.get(
            url,
            {
                "city": "Toronto",
                "lastName": "Smith",
                "locationType": LocationChoicesEnum.OTHER,
                "hasNotes": "true",
            },
        )

        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.vacation_address.id, result_ids)

    def test_filter_with_ordering(self):
        url = reverse("user-address-list")

        response = self.client.get(url, {"ordering": "-createdAt"})
        self.assertEqual(response.status_code, 200)

        results = response.data["results"]
        self.assertEqual(len(results), 3)

        address_ids = [r["id"] for r in results]
        self.assertEqual(address_ids[0], self.vacation_address.id)
        self.assertEqual(address_ids[1], self.work_address.id)
        self.assertEqual(address_ids[2], self.home_address.id)

        response = self.client.get(url, {"ordering": "-isMain,title"})
        self.assertEqual(response.status_code, 200)

        results = response.data["results"]
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0]["id"], self.home_address.id)

    def test_existing_filters_still_work(self):
        url = reverse("user-address-list")

        response = self.client.get(
            url,
            {
                "is_main": "true",
                "first_name": "John",
                "location_type": LocationChoicesEnum.HOME,
            },
        )
        self.assertEqual(response.status_code, 200)

        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.home_address.id, result_ids)

    def test_user_isolation(self):
        url = reverse("user-address-list")

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 3)
        self.assertIn(self.home_address.id, result_ids)
        self.assertIn(self.work_address.id, result_ids)
        self.assertIn(self.vacation_address.id, result_ids)
        self.assertNotIn(self.other_user_address.id, result_ids)

        self.client.force_authenticate(user=self.other_user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        result_ids = [r["id"] for r in response.data["results"]]
        self.assertGreaterEqual(len(result_ids), 1)

        self.assertNotIn(self.home_address.id, result_ids)
        self.assertNotIn(self.work_address.id, result_ids)
        self.assertNotIn(self.vacation_address.id, result_ids)

    def test_special_filters(self):
        url = reverse("user-address-list")

        response = self.client.get(url, {"title": "Home Address"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.home_address.id, result_ids)

        response = self.client.get(url, {"zipcode_exact": "10001"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.home_address.id, result_ids)

        response = self.client.get(url, {"street_number": "456"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.work_address.id, result_ids)
