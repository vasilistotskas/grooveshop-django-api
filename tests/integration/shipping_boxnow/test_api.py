"""Integration tests for BoxNow locker API endpoints.

Tests the actual DRF viewset responses through the Django test client.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from shipping_boxnow.factories import BoxNowLockerFactory


@pytest.mark.django_db
class TestLockerListEndpoint:
    """GET /api/v1/shipping/boxnow/lockers"""

    def setup_method(self):
        self.client = APIClient()

    def _url(self):
        return reverse("shipping-boxnow-locker-list")

    def test_list_lockers_anonymous_ok(self):
        """Anonymous requests to the locker list endpoint are allowed."""
        BoxNowLockerFactory.create_batch(3)
        response = self.client.get(self._url())

        assert response.status_code == status.HTTP_200_OK
        # DRF pagination envelope: results key.
        data = response.json()
        assert "results" in data
        # At least the 3 we created (others may exist from parallel workers,
        # but we can assert >= 3 within the current test DB).
        assert len(data["results"]) >= 3

    def test_list_returns_only_active_lockers(self):
        """Inactive lockers are excluded from the list response."""
        BoxNowLockerFactory(is_active=True, external_id="active-001")
        BoxNowLockerFactory(is_active=False, external_id="inactive-001")

        response = self.client.get(self._url())
        ids = [r["externalId"] for r in response.json()["results"]]

        assert "active-001" in ids
        assert "inactive-001" not in ids


@pytest.mark.django_db
class TestLockerRetrieveEndpoint:
    """GET /api/v1/shipping/boxnow/lockers/<pk>"""

    def setup_method(self):
        self.client = APIClient()

    def test_retrieve_existing_locker(self):
        """Retrieve a locker by its database pk."""
        locker = BoxNowLockerFactory(external_id="apm-9999")
        url = reverse("shipping-boxnow-locker-detail", args=[locker.pk])
        response = self.client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["externalId"] == "apm-9999"

    def test_retrieve_nonexistent_returns_404(self):
        """Fetching a non-existent pk returns 404."""
        url = reverse("shipping-boxnow-locker-detail", args=[999999])
        response = self.client.get(url)

        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
class TestLockerNearestEndpoint:
    """POST /api/v1/shipping/boxnow/lockers/nearest"""

    def setup_method(self):
        self.client = APIClient()

    def _url(self):
        return reverse("shipping-boxnow-locker-nearest")

    def test_nearest_calls_boxnow_api_and_returns_locker(self):
        """Happy path: mocked BoxNow client returns a locker dict."""
        mock_locker = {
            "id": "4",
            "type": "apm",
            "image": "",
            "lat": "38.0",
            "lng": "23.7",
            "title": "Test Locker",
            "name": "Chalandri Locker",
            "postal_code": "15232",
            "country": "GR",
            "note": "",
            "address_line_1": "Leoforos 125",
            "address_line_2": "",
            "region": "el-GR",
            "distance": 0.5,
        }

        mock_client = MagicMock()
        mock_client.return_value.find_closest_locker.return_value = mock_locker

        with patch("shipping_boxnow.client.BoxNowClient", mock_client):
            response = self.client.post(
                self._url(),
                {
                    "city": "Chalandri",
                    "street": "Leoforos 125",
                    "postalCode": "15232",
                },
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["id"] == "4"
        assert data["name"] == "Chalandri Locker"

    def test_nearest_missing_required_fields_returns_400(self):
        """Missing required body fields returns 400."""
        response = self.client.post(self._url(), {}, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_nearest_boxnow_api_error_returns_400(self):
        """BoxNow API error is surfaced as a 400 with detail."""
        from shipping_boxnow.exceptions import BoxNowAPIError

        api_err = BoxNowAPIError(
            400, code="P423", message="Nearby locker not found."
        )

        mock_client = MagicMock()
        mock_client.return_value.find_closest_locker.side_effect = api_err

        with patch("shipping_boxnow.client.BoxNowClient", mock_client):
            response = self.client.post(
                self._url(),
                {
                    "city": "Nowhere",
                    "street": "Empty St 1",
                    "postalCode": "99999",
                },
                format="json",
            )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        data = response.json()
        assert data["code"] == "P423"
