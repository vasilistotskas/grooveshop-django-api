"""Integration tests for the AcsStation viewset (Phase 2 picker)."""

from __future__ import annotations

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from shipping_acs.enum.shop_kind import AcsShopKind
from shipping_acs.factories import AcsStationFactory

pytestmark = pytest.mark.django_db


def test_list_defaults_to_locker_kinds_only():
    """The picker hits the list endpoint with no shopKind override —
    we must not flood it with general shop rows."""
    AcsStationFactory(
        external_id="LOCKER-A",
        shop_kind=AcsShopKind.SMARTPOINT_INBOUND,
        postal_code="11525",
    )
    AcsStationFactory(
        external_id="SHOP-B",
        shop_kind=AcsShopKind.SHOP,
        postal_code="11525",
    )

    client = APIClient()
    response = client.get(reverse("shipping-acs-station-list"))

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    results = body.get("results", body)
    codes = {row["externalId"] for row in results}
    assert "LOCKER-A" in codes
    assert "SHOP-B" not in codes


def test_list_shop_kind_override_surfaces_shops():
    AcsStationFactory(
        external_id="SHOP-X",
        shop_kind=AcsShopKind.SHOP,
        postal_code="10557",
    )
    client = APIClient()
    response = client.get(
        reverse("shipping-acs-station-list"),
        {"shopKind": AcsShopKind.SHOP.value},
    )

    assert response.status_code == status.HTTP_200_OK
    results = response.json().get("results", response.json())
    assert any(row["externalId"] == "SHOP-X" for row in results)


def test_nearest_returns_postcode_match():
    AcsStationFactory(
        external_id="ATH-1",
        shop_kind=AcsShopKind.SMARTPOINT_INBOUND,
        postal_code="11525",
        city="ΑΘΗΝΑ",
    )
    AcsStationFactory(
        external_id="THE-1",
        shop_kind=AcsShopKind.SMARTPOINT_INBOUND,
        postal_code="54630",
        city="ΘΕΣΣΑΛΟΝΙΚΗ",
    )

    client = APIClient()
    response = client.get(
        reverse("shipping-acs-station-nearest"),
        {"postalCode": "11525"},
    )
    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    codes = {row["externalId"] for row in body}
    assert codes == {"ATH-1"}


def test_nearest_falls_back_to_city_when_postcode_misses():
    AcsStationFactory(
        external_id="MOL-1",
        shop_kind=AcsShopKind.SMARTPOINT_INBOUND,
        postal_code="84500",
        city="ΜΟΛΟΣ",
    )

    client = APIClient()
    response = client.get(
        reverse("shipping-acs-station-nearest"),
        {"postalCode": "99999", "city": "ΜΟΛΟΣ"},
    )
    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert any(row["externalId"] == "MOL-1" for row in body)


def test_nearest_requires_postal_code():
    client = APIClient()
    response = client.get(reverse("shipping-acs-station-nearest"))
    assert response.status_code == status.HTTP_400_BAD_REQUEST


def test_list_excludes_inactive_stations():
    AcsStationFactory(
        external_id="ACTIVE-1",
        shop_kind=AcsShopKind.SMARTPOINT_INBOUND,
        is_active=True,
    )
    AcsStationFactory(
        external_id="INACTIVE-1",
        shop_kind=AcsShopKind.SMARTPOINT_INBOUND,
        is_active=False,
    )
    client = APIClient()
    response = client.get(reverse("shipping-acs-station-list"))
    results = response.json().get("results", response.json())
    codes = {row["externalId"] for row in results}
    assert "ACTIVE-1" in codes
    assert "INACTIVE-1" not in codes
