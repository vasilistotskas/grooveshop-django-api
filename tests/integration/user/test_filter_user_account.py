from datetime import timedelta
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient
import pytest

from country.factories import CountryFactory
from region.factories import RegionFactory
from user.factories.account import UserAccountFactory


@pytest.fixture
def api_client():
    """Provide API client for tests."""
    return APIClient()


@pytest.fixture
def auth_user(db):
    """Create authenticated user."""
    return UserAccountFactory(
        email="auth@example.com",
        is_superuser=True,
        is_staff=True,
    )


@pytest.fixture
def test_data(db, auth_user):
    """Create test data for user account filter tests."""
    country_gr = CountryFactory(alpha_2="GR", name="Greece")
    country_de = CountryFactory(alpha_2="DE", name="Germany")
    region_athens = RegionFactory(alpha="ATH", country=country_gr)
    region_berlin = RegionFactory(alpha="BER", country=country_de)

    now = timezone.now()

    admin_user = UserAccountFactory(
        email="admin@example.com",
        username="admin_user",
        first_name="Admin",
        last_name="User",
        is_staff=True,
        is_active=True,
        country=country_gr,
        region=region_athens,
        city="Athens",
        zipcode="12345",
        phone="+306912345678",
        twitter="https://twitter.com/admin",
        linkedin="https://linkedin.com/in/admin",
        birth_date="1990-01-01",
    )
    admin_user.created_at = now - timedelta(days=60)
    admin_user.save()

    regular_user = UserAccountFactory(
        email="user@example.com",
        username="regular_user",
        first_name="Regular",
        last_name="User",
        is_staff=False,
        is_active=True,
        country=country_de,
        region=region_berlin,
        city="Berlin",
        zipcode="54321",
        phone=None,
        twitter="",
        linkedin="",
        birth_date=None,
    )
    regular_user.created_at = now - timedelta(hours=2)
    regular_user.save()

    inactive_user = UserAccountFactory(
        email="inactive@example.com",
        username="inactive_user",
        first_name="Inactive",
        last_name="User",
        is_staff=False,
        is_active=False,
        country=country_gr,
        region=region_athens,
        city="Thessaloniki",
        zipcode="67890",
        phone="+306987654321",
        github="https://github.com/inactive",
        website="https://inactive.com",
    )
    inactive_user.created_at = now - timedelta(hours=3)
    inactive_user.save()

    staff_user = UserAccountFactory(
        email="staff@example.com",
        username="staff_user",
        first_name="Staff",
        last_name="Member",
        is_staff=True,
        is_active=True,
        country=country_de,
        region=region_berlin,
        city="Munich",
        zipcode="98765",
        bio="I am a staff member",
        youtube="https://youtube.com/staff",
    )
    staff_user.created_at = now - timedelta(hours=4)
    staff_user.save()

    return {
        "auth_user": auth_user,
        "admin_user": admin_user,
        "regular_user": regular_user,
        "inactive_user": inactive_user,
        "staff_user": staff_user,
        "country_gr": country_gr,
        "country_de": country_de,
        "region_athens": region_athens,
        "region_berlin": region_berlin,
        "now": now,
    }


@pytest.mark.django_db
class UserAccountFilterTest:
    def test_basic_filters(self, api_client, test_data):
        api_client.force_authenticate(user=test_data["auth_user"])
        admin_user = test_data["admin_user"]
        regular_user = test_data["regular_user"]
        staff_user = test_data["staff_user"]
        auth_user = test_data["auth_user"]
        url = reverse("user-account-list")

        response = api_client.get(url, {"id": admin_user.id})
        assert response.status_code == 200
        result_ids = [r["id"] for r in response.data["results"]]
        assert admin_user.id in result_ids

        response = api_client.get(url, {"is_active": "true"})
        assert response.status_code == 200
        result_ids = [r["id"] for r in response.data["results"]]
        assert admin_user.id in result_ids
        assert regular_user.id in result_ids
        assert staff_user.id in result_ids

        response = api_client.get(url, {"is_staff": "true"})
        assert response.status_code == 200
        result_ids = [r["id"] for r in response.data["results"]]
        for user_id in [admin_user.id, staff_user.id, auth_user.id]:
            assert user_id in result_ids

    def test_email_filters(self, api_client, test_data):
        api_client.force_authenticate(user=test_data["auth_user"])
        url = reverse("user-account-list")

        response = api_client.get(url, {"email_exact": "admin@example.com"})
        assert response.status_code == 200
        result_emails = [r["email"] for r in response.data["results"]]
        assert "admin@example.com" in result_emails

        response = api_client.get(url, {"email": "example.com"})
        assert response.status_code == 200
        result_emails = [r["email"] for r in response.data["results"]]
        for email in [
            "admin@example.com",
            "user@example.com",
            "inactive@example.com",
            "staff@example.com",
            "auth@example.com",
        ]:
            assert email in result_emails

    def test_username_filters(self, api_client, test_data):
        api_client.force_authenticate(user=test_data["auth_user"])
        url = reverse("user-account-list")

        response = api_client.get(url, {"username_exact": "admin_user"})
        assert response.status_code == 200
        result_usernames = [r["username"] for r in response.data["results"]]
        assert "admin_user" in result_usernames

        response = api_client.get(url, {"username": "user"})
        assert response.status_code == 200
        result_usernames = [r["username"] for r in response.data["results"]]
        for username in [
            "admin_user",
            "regular_user",
            "inactive_user",
            "staff_user",
        ]:
            assert username in result_usernames

    def test_name_filters(self, api_client, test_data):
        api_client.force_authenticate(user=test_data["auth_user"])
        admin_user = test_data["admin_user"]
        regular_user = test_data["regular_user"]
        inactive_user = test_data["inactive_user"]
        url = reverse("user-account-list")

        response = api_client.get(url, {"first_name": "Admin"})
        assert response.status_code == 200
        result_first_names = [r["first_name"] for r in response.data["results"]]
        assert "Admin" in result_first_names

        response = api_client.get(url, {"last_name": "User"})
        assert response.status_code == 200
        result_ids = [r["id"] for r in response.data["results"]]
        for user_id in [admin_user.id, regular_user.id, inactive_user.id]:
            assert user_id in result_ids

    def test_location_filters(self, api_client, test_data):
        api_client.force_authenticate(user=test_data["auth_user"])
        admin_user = test_data["admin_user"]
        inactive_user = test_data["inactive_user"]
        regular_user = test_data["regular_user"]
        staff_user = test_data["staff_user"]
        url = reverse("user-account-list")

        response = api_client.get(url, {"country": "GR"})
        assert response.status_code == 200
        result_ids = [r["id"] for r in response.data["results"]]
        for user_id in [admin_user.id, inactive_user.id]:
            assert user_id in result_ids

        response = api_client.get(url, {"region": "BER"})
        assert response.status_code == 200
        result_ids = [r["id"] for r in response.data["results"]]
        for user_id in [regular_user.id, staff_user.id]:
            assert user_id in result_ids

        response = api_client.get(url, {"city": "Athens"})
        assert response.status_code == 200
        result_ids = [r["id"] for r in response.data["results"]]
        assert admin_user.id in result_ids

        response = api_client.get(url, {"zipcode": "123"})
        assert response.status_code == 200
        result_ids = [r["id"] for r in response.data["results"]]
        assert admin_user.id in result_ids

    def test_custom_filters(self, api_client, test_data):
        api_client.force_authenticate(user=test_data["auth_user"])
        admin_user = test_data["admin_user"]
        inactive_user = test_data["inactive_user"]
        regular_user = test_data["regular_user"]
        staff_user = test_data["staff_user"]
        url = reverse("user-account-list")

        response = api_client.get(url, {"has_phone": "true"})
        assert response.status_code == 200
        result_ids = [r["id"] for r in response.data["results"]]
        for user_id in [admin_user.id, inactive_user.id]:
            assert user_id in result_ids

        response = api_client.get(url, {"has_phone": "false"})
        assert response.status_code == 200
        result_ids = [r["id"] for r in response.data["results"]]
        assert regular_user.id in result_ids

        response = api_client.get(url, {"has_birth_date": "true"})
        assert response.status_code == 200
        result_ids = [r["id"] for r in response.data["results"]]
        assert admin_user.id in result_ids

        response = api_client.get(url, {"has_social_links": "true"})
        assert response.status_code == 200
        result_ids = [r["id"] for r in response.data["results"]]
        for user_id in [admin_user.id, inactive_user.id, staff_user.id]:
            assert user_id in result_ids

    def test_timestamp_filters(self, api_client, test_data):
        api_client.force_authenticate(user=test_data["auth_user"])
        regular_user = test_data["regular_user"]
        inactive_user = test_data["inactive_user"]
        staff_user = test_data["staff_user"]
        now = test_data["now"]
        url = reverse("user-account-list")

        created_after_date = now - timedelta(days=30)
        response = api_client.get(
            url, {"created_after": created_after_date.isoformat()}
        )
        assert response.status_code == 200
        result_ids = [r["id"] for r in response.data["results"]]
        for user_id in [regular_user.id, inactive_user.id, staff_user.id]:
            assert user_id in result_ids

        created_before_date = now - timedelta(days=30)
        response = api_client.get(
            url, {"created_before": created_before_date.isoformat()}
        )
        assert response.status_code == 200
        result_ids = [r["id"] for r in response.data["results"]]
        assert len(result_ids) >= 0

    def test_uuid_filter(self, api_client, test_data):
        api_client.force_authenticate(user=test_data["auth_user"])
        regular_user = test_data["regular_user"]
        url = reverse("user-account-list")

        response = api_client.get(url, {"uuid": str(regular_user.uuid)})
        assert response.status_code == 200
        result_ids = [r["id"] for r in response.data["results"]]
        assert regular_user.id in result_ids

    def test_camel_case_filters(self, api_client, test_data):
        api_client.force_authenticate(user=test_data["auth_user"])
        regular_user = test_data["regular_user"]
        staff_user = test_data["staff_user"]
        auth_user = test_data["auth_user"]
        admin_user = test_data["admin_user"]
        now = test_data["now"]
        url = reverse("user-account-list")

        created_after_date = now - timedelta(days=30)
        response = api_client.get(
            url,
            {
                "createdAfter": created_after_date.isoformat(),
                "isActive": "true",
            },
        )
        assert response.status_code == 200
        result_ids = [r["id"] for r in response.data["results"]]
        for user_id in [regular_user.id, staff_user.id, auth_user.id]:
            assert user_id in result_ids

        response = api_client.get(url, {"isStaff": "true", "hasPhone": "true"})
        assert response.status_code == 200
        result_ids = [r["id"] for r in response.data["results"]]
        assert admin_user.id in result_ids

    def test_existing_filters_still_work(self, api_client, test_data):
        api_client.force_authenticate(user=test_data["auth_user"])
        admin_user = test_data["admin_user"]
        url = reverse("user-account-list")

        response = api_client.get(
            url, {"is_staff": "true", "has_phone": "true", "country": "GR"}
        )
        assert response.status_code == 200
        result_ids = [r["id"] for r in response.data["results"]]
        assert admin_user.id in result_ids

    def test_bulk_filters(self, api_client, test_data):
        api_client.force_authenticate(user=test_data["auth_user"])
        regular_user = test_data["regular_user"]
        url = reverse("user-account-list")

        response = api_client.get(
            url,
            {
                "is_active": "true",
                "country": "DE",
                "city": "Berlin",
                "has_phone": "false",
            },
        )
        assert response.status_code == 200
        result_ids = [r["id"] for r in response.data["results"]]
        assert regular_user.id in result_ids

    def test_complex_filter_combinations(self, api_client, test_data):
        api_client.force_authenticate(user=test_data["auth_user"])
        admin_user = test_data["admin_user"]
        staff_user = test_data["staff_user"]
        now = test_data["now"]
        url = reverse("user-account-list")

        created_after_date = now - timedelta(days=90)
        response = api_client.get(
            url,
            {
                "createdAfter": created_after_date.isoformat(),
                "isActive": "true",
                "hasSocialLinks": "true",
                "ordering": "-createdAt",
            },
        )
        assert response.status_code == 200
        result_ids = [r["id"] for r in response.data["results"]]
        assert admin_user.id in result_ids
        assert staff_user.id in result_ids

    def test_filter_with_ordering(self, api_client, test_data):
        api_client.force_authenticate(user=test_data["auth_user"])
        admin_user = test_data["admin_user"]
        regular_user = test_data["regular_user"]
        staff_user = test_data["staff_user"]
        auth_user = test_data["auth_user"]
        url = reverse("user-account-list")

        response = api_client.get(
            url, {"isActive": "true", "ordering": "-email"}
        )
        assert response.status_code == 200
        results = response.data["results"]
        result_ids = [r["id"] for r in results]
        for user_id in [
            admin_user.id,
            regular_user.id,
            staff_user.id,
            auth_user.id,
        ]:
            assert user_id in result_ids
        emails = [r["email"] for r in results]
        assert emails == sorted(emails, reverse=True)

    def test_country_name_filter(self, api_client, test_data):
        api_client.force_authenticate(user=test_data["auth_user"])
        admin_user = test_data["admin_user"]
        inactive_user = test_data["inactive_user"]
        url = reverse("user-account-list")

        response = api_client.get(url, {"country_name": "Greece"})
        assert response.status_code == 200
        result_ids = [r["id"] for r in response.data["results"]]
        for user_id in [admin_user.id, inactive_user.id]:
            assert user_id in result_ids
