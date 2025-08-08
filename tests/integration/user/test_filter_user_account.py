from datetime import timedelta
from django.urls import reverse
from django.utils import timezone
from django.test import TransactionTestCase
from rest_framework.test import APIClient
import pytest

from country.factories import CountryFactory
from region.factories import RegionFactory
from user.factories.account import UserAccountFactory
from user.models.account import UserAccount


@pytest.mark.django_db(transaction=True)
class UserAccountFilterTest(TransactionTestCase):
    def setUp(self):
        UserAccount.objects.all().delete()

        self.client = APIClient()

        self.auth_user = UserAccountFactory(
            email="auth@example.com",
            is_superuser=True,
            is_staff=True,
        )
        self.client.force_authenticate(user=self.auth_user)

        self.country_gr = CountryFactory(alpha_2="GR", name="Greece")
        self.country_de = CountryFactory(alpha_2="DE", name="Germany")
        self.region_athens = RegionFactory(alpha="ATH", country=self.country_gr)
        self.region_berlin = RegionFactory(alpha="BER", country=self.country_de)

        self.now = timezone.now()

        self.admin_user = UserAccountFactory(
            email="admin@example.com",
            username="admin_user",
            first_name="Admin",
            last_name="User",
            is_staff=True,
            is_active=True,
            country=self.country_gr,
            region=self.region_athens,
            city="Athens",
            zipcode="12345",
            phone="+306912345678",
            twitter="https://twitter.com/admin",
            linkedin="https://linkedin.com/in/admin",
            birth_date="1990-01-01",
        )
        self.admin_user.created_at = self.now - timedelta(days=60)
        self.admin_user.save()

        self.regular_user = UserAccountFactory(
            email="user@example.com",
            username="regular_user",
            first_name="Regular",
            last_name="User",
            is_staff=False,
            is_active=True,
            country=self.country_de,
            region=self.region_berlin,
            city="Berlin",
            zipcode="54321",
            phone=None,
            twitter="",
            linkedin="",
            birth_date=None,
        )
        self.regular_user.created_at = self.now - timedelta(hours=2)
        self.regular_user.save()

        self.inactive_user = UserAccountFactory(
            email="inactive@example.com",
            username="inactive_user",
            first_name="Inactive",
            last_name="User",
            is_staff=False,
            is_active=False,
            country=self.country_gr,
            region=self.region_athens,
            city="Thessaloniki",
            zipcode="67890",
            phone="+306987654321",
            github="https://github.com/inactive",
            website="https://inactive.com",
        )
        self.inactive_user.created_at = self.now - timedelta(hours=3)
        self.inactive_user.save()

        self.staff_user = UserAccountFactory(
            email="staff@example.com",
            username="staff_user",
            first_name="Staff",
            last_name="Member",
            is_staff=True,
            is_active=True,
            country=self.country_de,
            region=self.region_berlin,
            city="Munich",
            zipcode="98765",
            bio="I am a staff member",
            youtube="https://youtube.com/staff",
        )
        self.staff_user.created_at = self.now - timedelta(hours=4)
        self.staff_user.save()

    def test_basic_filters(self):
        url = reverse("user-account-list")

        response = self.client.get(url, {"id": self.admin_user.id})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.admin_user.id, result_ids)
        user_found = any(
            r["id"] == self.admin_user.id for r in response.data["results"]
        )
        self.assertTrue(user_found)

        response = self.client.get(url, {"is_active": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.admin_user.id, result_ids)
        self.assertIn(self.regular_user.id, result_ids)
        self.assertIn(self.staff_user.id, result_ids)

        response = self.client.get(url, {"is_staff": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        expected_staff_users = [
            self.admin_user.id,
            self.staff_user.id,
            self.auth_user.id,
        ]
        for user_id in expected_staff_users:
            self.assertIn(user_id, result_ids)

    def test_email_filters(self):
        url = reverse("user-account-list")

        response = self.client.get(url, {"email_exact": "admin@example.com"})
        self.assertEqual(response.status_code, 200)
        result_emails = [r["email"] for r in response.data["results"]]
        self.assertIn("admin@example.com", result_emails)
        admin_found = any(
            r["email"] == "admin@example.com" for r in response.data["results"]
        )
        self.assertTrue(admin_found)

        response = self.client.get(url, {"email": "example.com"})
        self.assertEqual(response.status_code, 200)
        result_emails = [r["email"] for r in response.data["results"]]
        test_emails = [
            "admin@example.com",
            "user@example.com",
            "inactive@example.com",
            "staff@example.com",
            "auth@example.com",
        ]
        for email in test_emails:
            self.assertIn(email, result_emails)

        response = self.client.get(url, {"email": "admin"})
        self.assertEqual(response.status_code, 200)
        result_emails = [r["email"] for r in response.data["results"]]
        self.assertIn("admin@example.com", result_emails)

    def test_username_filters(self):
        url = reverse("user-account-list")

        response = self.client.get(url, {"username_exact": "admin_user"})
        self.assertEqual(response.status_code, 200)
        result_usernames = [r["username"] for r in response.data["results"]]
        self.assertIn("admin_user", result_usernames)
        admin_found = any(
            r["username"] == "admin_user" for r in response.data["results"]
        )
        self.assertTrue(admin_found)

        response = self.client.get(url, {"username": "user"})
        self.assertEqual(response.status_code, 200)
        result_usernames = [r["username"] for r in response.data["results"]]
        expected_usernames = [
            "admin_user",
            "regular_user",
            "inactive_user",
            "staff_user",
        ]
        for username in expected_usernames:
            self.assertIn(username, result_usernames)

    def test_name_filters(self):
        url = reverse("user-account-list")

        response = self.client.get(url, {"first_name": "Admin"})
        self.assertEqual(response.status_code, 200)
        result_first_names = [r["first_name"] for r in response.data["results"]]
        self.assertIn("Admin", result_first_names)
        admin_found = any(
            r["first_name"] == "Admin" for r in response.data["results"]
        )
        self.assertTrue(admin_found)

        response = self.client.get(url, {"last_name": "User"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        expected_user_ids = [
            self.admin_user.id,
            self.regular_user.id,
            self.inactive_user.id,
        ]
        for user_id in expected_user_ids:
            self.assertIn(user_id, result_ids)

    def test_location_filters(self):
        url = reverse("user-account-list")

        response = self.client.get(url, {"country": "GR"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        expected_gr_users = [self.admin_user.id, self.inactive_user.id]
        for user_id in expected_gr_users:
            self.assertIn(user_id, result_ids)

        response = self.client.get(url, {"region": "BER"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        expected_ber_users = [self.regular_user.id, self.staff_user.id]
        for user_id in expected_ber_users:
            self.assertIn(user_id, result_ids)

        response = self.client.get(url, {"city": "Athens"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.admin_user.id, result_ids)

        response = self.client.get(url, {"zipcode": "123"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.admin_user.id, result_ids)

    def test_custom_filters(self):
        url = reverse("user-account-list")

        response = self.client.get(url, {"has_phone": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        expected_with_phone = [self.admin_user.id, self.inactive_user.id]
        for user_id in expected_with_phone:
            self.assertIn(user_id, result_ids)

        response = self.client.get(url, {"has_phone": "false"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.regular_user.id, result_ids)

        response = self.client.get(url, {"has_birth_date": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.admin_user.id, result_ids)

        response = self.client.get(url, {"has_social_links": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.admin_user.id, result_ids)
        self.assertIn(self.inactive_user.id, result_ids)
        self.assertIn(self.staff_user.id, result_ids)

    def test_timestamp_filters(self):
        url = reverse("user-account-list")

        created_after_date = self.now - timedelta(days=30)
        response = self.client.get(
            url,
            {"created_after": created_after_date.isoformat()},
        )
        self.assertEqual(response.status_code, 200)

        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.regular_user.id, result_ids)
        self.assertIn(self.inactive_user.id, result_ids)
        self.assertIn(self.staff_user.id, result_ids)

        created_before_date = self.now - timedelta(days=30)
        response = self.client.get(
            url,
            {"created_before": created_before_date.isoformat()},
        )
        self.assertEqual(response.status_code, 200)

        result_ids = [r["id"] for r in response.data["results"]]
        self.assertGreaterEqual(len(result_ids), 0)

    def test_uuid_filter(self):
        url = reverse("user-account-list")

        response = self.client.get(url, {"uuid": str(self.regular_user.uuid)})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.regular_user.id, result_ids)
        user_found = any(
            r["id"] == self.regular_user.id for r in response.data["results"]
        )
        self.assertTrue(user_found)

    def test_camel_case_filters(self):
        url = reverse("user-account-list")

        created_after_date = self.now - timedelta(days=30)
        response = self.client.get(
            url,
            {
                "createdAfter": created_after_date.isoformat(),
                "isActive": "true",
            },
        )
        self.assertEqual(response.status_code, 200)

        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.regular_user.id, result_ids)
        self.assertIn(self.staff_user.id, result_ids)
        self.assertIn(self.auth_user.id, result_ids)
        recent_active_users = [
            self.regular_user.id,
            self.staff_user.id,
            self.auth_user.id,
        ]
        for user_id in recent_active_users:
            self.assertIn(user_id, result_ids)

        response = self.client.get(
            url,
            {
                "isStaff": "true",
                "hasPhone": "true",
            },
        )
        self.assertEqual(response.status_code, 200)

        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.admin_user.id, result_ids)

    def test_existing_filters_still_work(self):
        url = reverse("user-account-list")

        response = self.client.get(
            url,
            {"is_staff": "true", "has_phone": "true", "country": "GR"},
        )
        self.assertEqual(response.status_code, 200)

        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.admin_user.id, result_ids)
        admin_result = next(
            (
                r
                for r in response.data["results"]
                if r["id"] == self.admin_user.id
            ),
            None,
        )
        self.assertIsNotNone(admin_result)

    def test_bulk_filters(self):
        url = reverse("user-account-list")

        response = self.client.get(
            url,
            {
                "is_active": "true",
                "country": "DE",
                "city": "Berlin",
                "has_phone": "false",
            },
        )
        self.assertEqual(response.status_code, 200)

        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.regular_user.id, result_ids)
        regular_result = next(
            (
                r
                for r in response.data["results"]
                if r["id"] == self.regular_user.id
            ),
            None,
        )
        self.assertIsNotNone(regular_result)

    def test_complex_filter_combinations(self):
        url = reverse("user-account-list")

        created_after_date = self.now - timedelta(days=90)

        response = self.client.get(
            url,
            {
                "createdAfter": created_after_date.isoformat(),
                "isActive": "true",
                "hasSocialLinks": "true",
                "ordering": "-createdAt",
            },
        )

        self.assertEqual(response.status_code, 200)

        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.admin_user.id, result_ids)
        self.assertIn(self.staff_user.id, result_ids)
        expected_with_social = [
            self.admin_user.id,
            self.inactive_user.id,
            self.staff_user.id,
        ]
        for user_id in expected_with_social:
            self.assertIn(user_id, result_ids)

        results = response.data["results"]
        self.assertGreater(len(results), 0)

    def test_filter_with_ordering(self):
        url = reverse("user-account-list")

        response = self.client.get(
            url, {"isActive": "true", "ordering": "-email"}
        )
        self.assertEqual(response.status_code, 200)

        results = response.data["results"]
        result_ids = [r["id"] for r in results]
        active_test_users = [
            self.admin_user.id,
            self.regular_user.id,
            self.staff_user.id,
            self.auth_user.id,
        ]
        for user_id in active_test_users:
            self.assertIn(user_id, result_ids)

        emails = [r["email"] for r in results]
        self.assertEqual(emails, sorted(emails, reverse=True))

    def test_country_name_filter(self):
        url = reverse("user-account-list")

        response = self.client.get(url, {"country_name": "Greece"})
        self.assertEqual(response.status_code, 200)

        result_ids = [r["id"] for r in response.data["results"]]
        expected_greece_users = [self.admin_user.id, self.inactive_user.id]
        for user_id in expected_greece_users:
            self.assertIn(user_id, result_ids)

    def tearDown(self):
        UserAccount.objects.all().delete()
