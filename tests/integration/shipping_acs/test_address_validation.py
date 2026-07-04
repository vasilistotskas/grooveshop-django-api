"""Integration tests for the Phase 4b address-validation endpoint."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from shipping_acs.exceptions import AcsAPIError, AcsConfigError

pytestmark = pytest.mark.django_db


_ACS_RAW_RESPONSE = {
    "GeoID": 631466,
    "Resolved_Street": "ΠΕΙΡΑΙΩΣ",
    "Resolved_Street_Num": "25",
    "Resolved_Zip": "17778",
    "Resolved_Area": "ΤΑΥΡΟΣ",
    "Resolved_Long": 23.67857,
    "Resolved_Lat": 38.04628,
    "Resolved_Station_ID": "TVR",
    "Resolved_Branch_ID": 1,
    "Resolved_Providence": "ΑΘΗΝΩΝ",
    "AddressID": "abc-123",
}


def test_missing_address_field_returns_400():
    client = APIClient()
    response = client.post(
        reverse("shipping-acs-address-validation"),
        {},
        format="json",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


def test_validates_address_via_acs_client():
    client = APIClient()
    with patch("shipping_acs.client.AcsClient") as mock_class:
        instance = mock_class.return_value
        instance.address_validation.return_value = _ACS_RAW_RESPONSE
        response = client.post(
            reverse("shipping-acs-address-validation"),
            {"address": "ΠΕΙΡΑΙΩΣ 25 17778"},
            format="json",
        )

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    # camelCase via drf_camel_case middleware.
    assert body["resolvedZip"] == "17778"
    assert body["resolvedStationId"] == "TVR"
    assert instance.address_validation.called


def test_acs_config_error_returns_503():
    client = APIClient()
    with patch("shipping_acs.client.AcsClient") as mock_class:
        mock_class.side_effect = AcsConfigError("creds missing")
        response = client.post(
            reverse("shipping-acs-address-validation"),
            {"address": "x x x"},
            format="json",
        )
    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE


def test_acs_api_error_returns_502():
    client = APIClient()
    with patch("shipping_acs.client.AcsClient") as mock_class:
        instance = mock_class.return_value
        instance.address_validation.side_effect = AcsAPIError(
            alias="ACS_Address_Validation",
            error_message="bad address",
        )
        response = client.post(
            reverse("shipping-acs-address-validation"),
            {"address": "x x x"},
            format="json",
        )
    assert response.status_code == status.HTTP_502_BAD_GATEWAY


def test_acs_read_timeout_returns_502_not_500():
    """Regression: prod 2026-07-04 — a 15s ACS read timeout escaped
    the client unwrapped and surfaced as an unhandled 500 during
    checkout. The client now wraps ``requests.Timeout`` into
    ``AcsRetryableError`` (an ``AcsAPIError`` subclass), which this
    view maps to a clean 502."""
    import requests as requests_lib

    from shipping_acs.client import AcsClient as RealAcsClient

    client = APIClient()
    session = MagicMock()
    session.post.side_effect = requests_lib.ReadTimeout("read timed out")
    acs_client = RealAcsClient(
        api_key="k",
        company_id="c",
        company_password="p",
        user_id="u",
        user_password="up",
        session=session,
    )
    with patch("shipping_acs.client.AcsClient") as mock_class:
        mock_class.return_value = acs_client
        response = client.post(
            reverse("shipping-acs-address-validation"),
            {"address": "ΠΕΙΡΑΙΩΣ 25 17778 timeout-case"},
            format="json",
        )
    assert response.status_code == status.HTTP_502_BAD_GATEWAY


def test_repeated_calls_hit_cache_not_api():
    """Identical address strings must short-circuit on the Redis
    cache so a typing storm of 8 keystrokes ≠ 8 ACS API calls."""
    client = APIClient()
    with patch("shipping_acs.client.AcsClient") as mock_class:
        instance = mock_class.return_value
        instance.address_validation.return_value = _ACS_RAW_RESPONSE
        client.post(
            reverse("shipping-acs-address-validation"),
            {"address": "ΠΕΙΡΑΙΩΣ 25 17778"},
            format="json",
        )
        client.post(
            reverse("shipping-acs-address-validation"),
            {"address": "ΠΕΙΡΑΙΩΣ 25 17778"},
            format="json",
        )

    assert instance.address_validation.call_count == 1
