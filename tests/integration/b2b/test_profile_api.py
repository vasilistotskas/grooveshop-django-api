"""/api/v1/b2b/profile — submit/read lifecycle and the two-tier gate."""

from __future__ import annotations

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from b2b.enum import BusinessProfileStatus
from b2b.factories import BusinessProfileFactory
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

PROFILE_PAYLOAD = {
    "company_name": "Example IKE",
    "vat_id": "EL123456783",
    "tax_office": "Α' Αθηνών",
    "activity": "Retail trade",
    "billing_street": "Ermou",
    "billing_street_number": "1",
    "billing_city": "Athens",
    "billing_zipcode": "10563",
}


def _client(user=None):
    client = APIClient()
    if user is not None:
        client.force_authenticate(user=user)
    return client


class TestProfileEndpoint:
    def test_get_without_profile_404s_with_reason(
        self, b2b_tenant, enable_wholesale
    ):
        response = _client(UserAccountFactory()).get(reverse("b2b:b2b-profile"))

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.data["reason"] == "no_business_profile"

    def test_submit_creates_pending_profile_with_vies_snapshot(
        self, b2b_tenant, enable_wholesale, mock_vies
    ):
        user = UserAccountFactory()

        response = _client(user).put(
            reverse("b2b:b2b-profile"), PROFILE_PAYLOAD, format="json"
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["status"] == BusinessProfileStatus.PENDING
        # EL prefix is stripped at the serializer boundary. Keys are
        # snake_case here — the camelCase renderer transforms on
        # RENDER, and DRF's test ``response.data`` is pre-render.
        assert response.data["vat_id"] == "123456783"
        assert response.data["vies_status"] == "VALID"
        assert response.data["vies_name"] == "EXAMPLE IKE"
        mock_vies.assert_called_once_with("EL", "123456783")

    def test_checksum_invalid_vat_rejected(
        self, b2b_tenant, enable_wholesale, mock_vies
    ):
        response = _client(UserAccountFactory()).put(
            reverse("b2b:b2b-profile"),
            {**PROFILE_PAYLOAD, "vat_id": "123456789"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        mock_vies.assert_not_called()

    def test_identity_edit_while_approved_resets_to_pending(
        self, b2b_tenant, enable_wholesale, mock_vies
    ):
        profile = BusinessProfileFactory(approved=True)

        response = _client(profile.user).put(
            reverse("b2b:b2b-profile"), PROFILE_PAYLOAD, format="json"
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == BusinessProfileStatus.PENDING

    def test_get_returns_own_profile(self, b2b_tenant, enable_wholesale):
        profile = BusinessProfileFactory(approved=True)

        response = _client(profile.user).get(reverse("b2b:b2b-profile"))

        assert response.status_code == status.HTTP_200_OK
        assert response.data["company_name"] == profile.company_name
        assert (
            response.data["customer_group_name"] == profile.customer_group.name
        )


class TestGates:
    def test_plan_flag_off_404s(self, b2b_tenant, enable_wholesale):
        b2b_tenant.b2b_enabled = False
        b2b_tenant.save(update_fields=["b2b_enabled"])

        response = _client(UserAccountFactory()).get(reverse("b2b:b2b-profile"))

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_runtime_setting_off_404s(self, b2b_tenant):
        # No enable_wholesale fixture — the setting defaults to False,
        # and IsB2BWholesaleEnabled fails CLOSED.
        response = _client(UserAccountFactory()).get(reverse("b2b:b2b-profile"))

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_anonymous_gets_auth_error_not_404(
        self, b2b_tenant, enable_wholesale
    ):
        response = _client().get(reverse("b2b:b2b-profile"))

        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )
