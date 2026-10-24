"""Integration tests for GET /api/v1/shipping/policy."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from country.factories import CountryFactory
from shipping.enum import ShippingKind
from shipping.models import ShippingProvider, ShippingRate
from shipping_acs.models import AcsStation
from tests.utils.shipping import enable_rate

pytestmark = pytest.mark.django_db

ROW_KEYS = {
    "countryCode",
    "kind",
    "providerCode",
    "providerName",
    "price",
    "currency",
    "freeShippingThreshold",
    "deliveryDaysMin",
    "deliveryDaysMax",
    "maxWeightGrams",
}


@pytest.fixture
def _acs_only(acs_configured_tenant, acs_locker_station):
    ShippingProvider.objects.filter(code="acs").update(is_active=True)
    ShippingProvider.objects.exclude(code="acs").update(is_active=False)


def _get() -> list[dict]:
    response = APIClient().get(reverse("shipping-policy"))
    assert response.status_code == status.HTTP_200_OK
    return response.json()


def test_an_anonymous_visitor_gets_the_policy_shape(_acs_only):
    body = _get()

    assert body
    assert all(set(row) == ROW_KEYS for row in body)
    home = next(row for row in body if row["kind"] == "home_delivery")
    assert home["countryCode"] == "GR"
    assert home["providerCode"] == "acs"
    assert home["currency"] == "EUR"


def test_a_store_that_ships_nowhere_returns_an_empty_list():
    ShippingProvider.objects.update(is_active=False)

    assert _get() == []


def test_only_eligible_rows_are_listed(_acs_only):
    ShippingRate.objects.filter(
        provider__code="acs", kind=ShippingKind.HOME_DELIVERY.value
    ).update(is_active=False)

    assert {row["kind"] for row in _get()} == {"pickup_point"}


def test_acs_pickup_is_left_out_without_stations(_acs_only):
    AcsStation.objects.update(is_active=False)

    assert {row["kind"] for row in _get()} == {"home_delivery"}


def test_an_inactive_provider_is_left_out(_acs_only):
    ShippingProvider.objects.filter(code="acs").update(is_active=False)

    assert _get() == []


def test_two_countries_each_get_their_own_rate(_acs_only):
    enable_rate(
        CountryFactory(alpha_2="DE"),
        provider_code="acs",
        price=Decimal("9.90"),
        free_shipping_threshold=None,
    )

    home = {
        row["countryCode"]: row
        for row in _get()
        if row["kind"] == "home_delivery"
    }

    assert set(home) == {"GR", "DE"}
    assert Decimal(str(home["DE"]["price"])) == Decimal("9.90")
    assert home["DE"]["freeShippingThreshold"] is None
