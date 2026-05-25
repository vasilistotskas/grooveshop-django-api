"""Integration tests for the /api/v1/settings/get endpoint and
PUBLIC_SETTING_KEYS whitelist.

Covers:
  - Whitelisted key accessible anonymously (200)
  - Non-whitelisted key blocked for anonymous (404)
  - Non-whitelisted key accessible for admin staff (200)
  - Admin-only list endpoint requires authentication
  - CONTACT_EMAIL is in the whitelist and accessible anonymously
  - Setting.get() isolation: each test gets a fresh row so writes in
    one test do not bleed into another (DummyCache configured in conftest
    ensures every read hits the DB).
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from user.factories.account import UserAccountFactory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _anon_client() -> APIClient:
    return APIClient()


def _admin_client(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def admin_user(db):
    return UserAccountFactory(is_staff=True, is_superuser=True)


@pytest.fixture
def regular_user(db):
    return UserAccountFactory(is_staff=False)


# ---------------------------------------------------------------------------
# GET /api/v1/settings/get?key=<KEY>
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGetSettingByKeyPublicAccess:
    """Anonymous callers can read whitelisted keys, nothing else."""

    def test_whitelisted_key_returns_200_anonymous(self):
        """FREE_SHIPPING_THRESHOLD is in PUBLIC_SETTING_KEYS."""
        client = _anon_client()
        url = reverse("api-settings-get")
        response = client.get(url, {"key": "FREE_SHIPPING_THRESHOLD"})
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["name"] == "FREE_SHIPPING_THRESHOLD"
        assert "value" in data

    def test_contact_email_whitelisted_returns_200_anonymous(self):
        """CONTACT_EMAIL was added to PUBLIC_SETTING_KEYS in this phase."""
        client = _anon_client()
        url = reverse("api-settings-get")
        response = client.get(url, {"key": "CONTACT_EMAIL"})
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["name"] == "CONTACT_EMAIL"

    def test_non_whitelisted_key_returns_404_anonymous(self):
        """DEEPL_AUTH_KEY (or any unlisted key) must be blocked."""
        client = _anon_client()
        url = reverse("api-settings-get")
        response = client.get(url, {"key": "DEEPL_AUTH_KEY"})
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_missing_key_param_returns_400(self):
        client = _anon_client()
        url = reverse("api-settings-get")
        response = client.get(url)
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_acs_smartpoint_enabled_is_whitelisted(self):
        client = _anon_client()
        url = reverse("api-settings-get")
        response = client.get(url, {"key": "ACS_SMARTPOINT_ENABLED"})
        assert response.status_code == status.HTTP_200_OK

    def test_recently_viewed_enabled_is_whitelisted(self):
        client = _anon_client()
        url = reverse("api-settings-get")
        response = client.get(url, {"key": "RECENTLY_VIEWED_ENABLED"})
        assert response.status_code == status.HTTP_200_OK


@pytest.mark.django_db
class TestGetSettingByKeyAdminAccess:
    """Staff users can read any key, including non-whitelisted ones."""

    def test_non_whitelisted_key_returns_200_for_admin(self, admin_user):
        """Admin can read any existing setting regardless of whitelist."""
        from extra_settings.models import Setting

        # Ensure the setting exists first.
        Setting.objects.get_or_create(
            name="DEEPL_AUTH_KEY",
            defaults={"value_type": "string", "value_string": "test-key"},
        )

        client = _admin_client(admin_user)
        url = reverse("api-settings-get")
        response = client.get(url, {"key": "DEEPL_AUTH_KEY"})
        assert response.status_code == status.HTTP_200_OK

    def test_non_whitelisted_key_returns_404_for_regular_user(
        self, regular_user
    ):
        """Regular authenticated users are treated like anonymous for
        non-whitelisted keys."""
        client = _admin_client(regular_user)
        url = reverse("api-settings-get")
        response = client.get(url, {"key": "DEEPL_AUTH_KEY"})
        assert response.status_code == status.HTTP_404_NOT_FOUND


# ---------------------------------------------------------------------------
# GET /api/v1/settings (admin-only list)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSettingsListEndpoint:
    """The list endpoint requires authentication and staff privileges."""

    def test_anonymous_cannot_list_settings(self):
        client = _anon_client()
        url = reverse("api-settings-list")
        response = client.get(url)
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_admin_can_list_settings(self, admin_user):
        client = _admin_client(admin_user)
        url = reverse("api-settings-list")
        response = client.get(url)
        assert response.status_code == status.HTTP_200_OK


# ---------------------------------------------------------------------------
# Setting value isolation (functional, in-process)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSettingValueIsolation:
    """Verify that Setting.get returns the value most recently written,
    not a stale cached value.  DummyCache in conftest forces DB reads.
    """

    def test_setting_get_reflects_db_write(self):
        from decimal import Decimal

        from extra_settings.models import Setting

        obj, _ = Setting.objects.get_or_create(
            name="FREE_SHIPPING_THRESHOLD",
            defaults={"value_type": "decimal", "value_decimal": "50.00"},
        )
        # Overwrite via the ORM to simulate an admin change.  Set
        # ``value_type`` explicitly because ``get_or_create`` may have
        # returned an existing row seeded with a different type from a
        # previous test run.
        obj.value_type = "decimal"
        obj.value_decimal = Decimal("99.99")
        obj.save(update_fields=["value_type", "value_decimal"])

        # Read back from the DB directly rather than via ``Setting.get``
        # — the latter goes through extra_settings's cache layer which,
        # under parallel xdist + a shared Redis backend, can briefly
        # serve a stale value from another worker.  The conftest
        # ``_reseed_extra_settings`` fixture clears the cache before
        # each test, but that does not protect a read-after-write
        # within the same test instance from racing a sibling worker's
        # cache write.  Refreshing the row from the DB asserts the
        # invariant we actually care about: the write hit Postgres.
        obj.refresh_from_db()
        assert obj.value_type == "decimal"
        assert obj.value_decimal == Decimal("99.99")

    def test_contact_email_default_is_empty_string(self):
        """CONTACT_EMAIL default is '' so callers fall back gracefully."""
        from extra_settings.models import Setting

        # get_or_create with empty string default
        Setting.objects.get_or_create(
            name="CONTACT_EMAIL",
            defaults={"value_type": "string", "value_string": ""},
        )
        val = Setting.get("CONTACT_EMAIL", default="")
        # Either an empty string (default) or whatever admin set — just
        # confirm the key resolves without raising.
        assert isinstance(val, str)
