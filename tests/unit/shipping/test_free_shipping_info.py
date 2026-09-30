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
from unittest.mock import patch

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

pytestmark = pytest.mark.django_db

# ``ACS_SMARTPOINT_ENABLED`` is the only Setting ``is_kind_enabled``
# still reads — pricing is entirely ``ShippingRate`` now. Mocked
# rather than written via ``Setting.objects.update_or_create``, so the
# tests do not depend on the ``Setting`` table.
_DEFAULT_SETTING_OVERRIDES: dict[str, object] = {
    "ACS_SMARTPOINT_ENABLED": True,
}


def _build_fake_setting_get(overrides: dict[str, object]):
    def fake_get(name: str, default=None):
        if name in overrides:
            return overrides[name]
        return default

    return fake_get


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
def _activate_acs_only(acs_configured_tenant):
    """Make ACS the sole active provider with both kinds enabled.

    Mirrors a real deploy where ops have flipped ACS on but BoxNow
    is still off (BoxNow ships disabled by default per the seed). The
    GR rates for both are already in place — the shipping migrations
    seed them (``tests/migration_seed.py`` keeps them). Yields the Setting overrides dict so individual
    tests can mutate it (e.g. flipping ``ACS_SMARTPOINT_ENABLED`` to
    False). Also binds a tenant with ACS credentials —
    ``is_kind_enabled`` hides ACS entirely for an unconfigured tenant
    regardless of ``is_active``.
    """
    ShippingProvider.objects.filter(code="acs").update(is_active=True)
    ShippingProvider.objects.filter(code="boxnow").update(is_active=False)
    overrides = dict(_DEFAULT_SETTING_OVERRIDES)
    with patch(
        "extra_settings.models.Setting.get",
        side_effect=_build_fake_setting_get(overrides),
    ):
        yield overrides


@pytest.fixture
def _activate_both(acs_and_boxnow_configured_tenant):
    """Activate both ACS and BoxNow with Smartpoint enabled.

    Yields the Setting overrides dict so individual tests can mutate
    per-carrier flags before exercising the code path. Binds a tenant
    with BOTH ACS and BoxNow credentials configured — both carriers
    are now gated on tenant credentials via ``is_kind_enabled``,
    regardless of ``ShippingProvider.is_active``.
    """
    ShippingProvider.objects.filter(code__in=["acs", "boxnow"]).update(
        is_active=True
    )
    overrides = dict(_DEFAULT_SETTING_OVERRIDES)
    with patch(
        "extra_settings.models.Setting.get",
        side_effect=_build_fake_setting_get(overrides),
    ):
        yield overrides


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


def test_acs_smartpoint_disabled_hides_pickup(_activate_acs_only):
    # Flip ``ACS_SMARTPOINT_ENABLED`` to False via the mock dict the
    # fixture yields. ``Setting.get`` is patched, so the override
    # takes effect immediately without a DB write.
    _activate_acs_only["ACS_SMARTPOINT_ENABLED"] = False
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
