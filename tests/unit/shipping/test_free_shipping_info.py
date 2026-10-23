"""Tests for ``ShippingService.free_shipping_info`` + its DRF view.

Covers:
* Active+registered providers contribute one row per supported kind.
* Inactive providers are filtered out.
* ``country_code`` matches against active ``ShippingRate`` rows.
* ``is_kind_enabled`` gating (e.g. ACS Smartpoint).
* Rates with no ``free_shipping_threshold`` are excluded (no "free
  above €0" leak).
* ``min_threshold`` / ``max_threshold`` aggregate correctly.
* Endpoint is anonymous and matches the serialized shape.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, ClassVar

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from country.factories import CountryFactory
from country.models import Country
from shipping.enum import ShippingKind
from shipping.factories import ShippingRateFactory
from shipping.interfaces import (
    _REGISTRY,
    ShippingCarrierInterface,
    register_provider,
)
from shipping.models import ShippingProvider
from shipping.services import ShippingService
from shipping_acs.models import AcsStation

pytestmark = pytest.mark.django_db


class _NoThresholdCarrier(ShippingCarrierInterface):
    """Concrete adapter used to test a rate with no threshold."""

    code: ClassVar[str] = "no_threshold_test"

    def create_shipment(self, order, *, kind, payload=None):
        return None

    def cancel_shipment(self, shipment, *, reason="") -> None:
        return None

    def fetch_label_bytes(self, shipment) -> bytes:
        return b""

    def fetch_tracking_events(self, shipment) -> list[dict[str, Any]]:
        return []

    def shipment_for_order(self, order):
        return None

    def serialize_shipment(self, shipment, *, context):
        return None

    def validate_order_payload(self, *, kind, payload) -> dict[str, list[str]]:
        return {}


@pytest.fixture
def cleanup_no_threshold():
    yield
    _REGISTRY.pop(_NoThresholdCarrier.code, None)
    ShippingProvider.objects.filter(code=_NoThresholdCarrier.code).delete()


@pytest.fixture
def _activate_acs_only(acs_configured_tenant, acs_locker_station):
    """Make ACS the sole active provider with both kinds enabled.

    Mirrors a real deploy where ops have flipped ACS on but BoxNow
    is still off (BoxNow ships disabled by default per the seed). The
    GR rates for both are already in place — the shipping migrations
    seed them (``tests/migration_seed.py`` keeps them) — and one GR
    locker is cached, the row the nightly sync leaves behind. Also
    binds a tenant with ACS credentials — ``is_kind_enabled`` hides ACS
    entirely for an unconfigured tenant regardless of ``is_active``.
    """
    ShippingProvider.objects.filter(code="acs").update(is_active=True)
    ShippingProvider.objects.filter(code="boxnow").update(is_active=False)


@pytest.fixture
def _activate_both(acs_and_boxnow_configured_tenant, acs_locker_station):
    """Activate both ACS and BoxNow, ACS with a cached GR locker.

    Binds a tenant with BOTH ACS and BoxNow credentials configured —
    both carriers are gated on tenant credentials via
    ``is_kind_enabled``, regardless of ``ShippingProvider.is_active``.
    """
    ShippingProvider.objects.filter(code__in=["acs", "boxnow"]).update(
        is_active=True
    )


def test_default_currency_is_eur():
    info = ShippingService.free_shipping_info()
    assert info["currency"] == "EUR"


def test_no_active_providers_returns_empty_aggregate():
    ShippingProvider.objects.filter(
        code__in=["acs", "boxnow", "flat_rate"]
    ).update(is_active=False)
    info = ShippingService.free_shipping_info()
    assert info["providers"] == []
    assert info["min_threshold"] is None
    assert info["max_threshold"] is None
    assert info["country_code"] is None


def test_acs_only_emits_both_kinds(_activate_acs_only):
    info = ShippingService.free_shipping_info()
    assert info["country_code"] == "GR"
    kinds = {row["kind"] for row in info["providers"]}
    assert kinds == {
        ShippingKind.HOME_DELIVERY.value,
        ShippingKind.PICKUP_POINT.value,
    }
    assert {row["provider_code"] for row in info["providers"]} == {"acs"}


def test_acs_without_stations_hides_pickup(_activate_acs_only):
    # The nightly sync has not filled (or has deactivated) the
    # country's lockers: the picker would be empty, so no pickup row.
    AcsStation.objects.update(is_active=False)
    info = ShippingService.free_shipping_info()
    kinds = {row["kind"] for row in info["providers"]}
    assert kinds == {ShippingKind.HOME_DELIVERY.value}


def test_boxnow_only_emits_pickup_point_only(_activate_both):
    ShippingProvider.objects.filter(code="acs").update(is_active=False)
    info = ShippingService.free_shipping_info()
    assert {row["provider_code"] for row in info["providers"]} == {"boxnow"}
    assert {row["kind"] for row in info["providers"]} == {
        ShippingKind.PICKUP_POINT.value
    }


def test_min_threshold_picks_smallest(_activate_both):
    # The autouse GR seed pins ACS at 40.00 and BoxNow at 30.00 (the
    # same frozen historical defaults ``0011_convert_legacy_pricing_
    # to_rates`` writes), so the aggregation should pick 30/40.
    info = ShippingService.free_shipping_info()
    assert info["min_threshold"] == Decimal("30.00")
    assert info["max_threshold"] == Decimal("40.00")


def test_country_code_filter_excludes_countries_with_no_rate(
    _activate_acs_only,
):
    """Only GR has an ACS rate (the autouse seed); DE has none."""
    matching = ShippingService.free_shipping_info(country_code="GR")
    non_matching = ShippingService.free_shipping_info(country_code="DE")
    assert len(matching["providers"]) > 0
    assert matching["country_code"] == "GR"
    assert non_matching["providers"] == []
    assert non_matching["country_code"] == "DE"


def test_defaults_to_the_first_shippable_country_when_omitted(
    _activate_acs_only,
):
    """No ``country_code`` ⇒ the first :meth:`shippable_country_codes`
    entry — the same rule checkout uses for its initial country."""
    info = ShippingService.free_shipping_info()
    assert info["country_code"] in ShippingService.shippable_country_codes()


def test_none_threshold_carrier_excluded(cleanup_no_threshold):
    register_provider(_NoThresholdCarrier)
    provider = ShippingProvider.objects.create(
        code=_NoThresholdCarrier.code,
        name="No-Threshold Test Carrier",
        is_active=True,
        supports_home_delivery=True,
        supports_pickup_point=False,
    )
    country = CountryFactory()
    ShippingRateFactory.create(
        provider=provider,
        country=country,
        kind=ShippingKind.HOME_DELIVERY,
        free_shipping_threshold=None,
    )
    info = ShippingService.free_shipping_info(country_code=country.alpha_2)
    codes = {row["provider_code"] for row in info["providers"]}
    assert _NoThresholdCarrier.code not in codes


def test_rate_of_an_unregistered_carrier_is_skipped(_activate_acs_only):
    """An active provider whose app is not installed has no adapter: its
    rate must neither surface as a row nor drag the advertised minimum
    below what a real carrier offers."""
    ghost = ShippingProvider.objects.create(
        code="ghost_carrier",
        name="Ghost Carrier",
        is_active=True,
        supports_home_delivery=True,
        supports_pickup_point=False,
    )
    ShippingRateFactory.create(
        provider=ghost,
        country=Country.objects.get(alpha_2="GR"),
        kind=ShippingKind.HOME_DELIVERY,
        free_shipping_threshold="5.00",
    )

    info = ShippingService.free_shipping_info(country_code="GR")

    assert {row["provider_code"] for row in info["providers"]} == {"acs"}
    assert info["min_threshold"] == Decimal("40.00")


def test_endpoint_is_anonymous_and_returns_serialised_shape(_activate_both):
    client = APIClient()
    url = reverse("shipping-free-shipping-info")
    response = client.get(url)
    assert response.status_code == status.HTTP_200_OK

    body = response.json()
    # camelCase via djangorestframework-camel-case
    assert "providers" in body
    assert "minThreshold" in body
    assert "maxThreshold" in body
    assert body["currency"] == "EUR"
    assert body["countryCode"] == "GR"

    assert all(
        {
            "providerCode",
            "providerName",
            "kind",
            "threshold",
            "priority",
        }
        <= set(row.keys())
        for row in body["providers"]
    )
