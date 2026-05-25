"""Tests for ``ShippingService.free_shipping_info`` + its DRF view.

Covers:
* Active+registered providers contribute one row per supported kind.
* Inactive providers are filtered out.
* ``country_code`` honours ``metadata['supported_countries']``.
* ``is_kind_enabled`` gating (e.g. ACS Smartpoint).
* ``None`` thresholds are excluded (no "free above €0" leak).
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

from shipping.enum import ShippingKind
from shipping.interfaces import (
    _REGISTRY,
    ShippingCarrierInterface,
    register_provider,
)
from shipping.models import ShippingProvider
from shipping.services import ShippingService

pytestmark = pytest.mark.django_db


# Mock-driven Setting overrides for xdist safety: writes via
# ``Setting.objects.update_or_create`` race the ``_reseed_extra_
# settings`` autouse fixture's savepoint visibility under parallel
# workers (see ``project_settings_update_or_create_flake.md``).
# Mirrors the pattern in ``tests/integration/loyalty/test_loyalty_
# lifecycle.py`` and ``tests/unit/cart/test_create_payment_intent_
# shipping.py``.
_DEFAULT_SETTING_OVERRIDES: dict[str, object] = {
    "ACS_FREE_SHIPPING_THRESHOLD": Decimal("40.00"),
    "BOXNOW_FREE_SHIPPING_THRESHOLD": Decimal("30.00"),
    "FREE_SHIPPING_THRESHOLD": Decimal("50.00"),
    "ACS_SMARTPOINT_ENABLED": True,
}


def _build_fake_setting_get(overrides: dict[str, object]):
    def fake_get(name: str, default=None):
        if name in overrides:
            return overrides[name]
        return default

    return fake_get


class _NoThresholdCarrier(ShippingCarrierInterface):
    """Concrete adapter that returns ``None`` from the new hook."""

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
def _activate_acs_only():
    """Make ACS the sole active provider with both kinds enabled.

    Mirrors a real deploy where ops have flipped ACS on but BoxNow
    is still off (BoxNow ships disabled by default per the seed).
    Yields the Setting overrides dict so individual tests can mutate
    it (e.g. flipping ``ACS_SMARTPOINT_ENABLED`` to False).
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
def _activate_both():
    """Activate both ACS and BoxNow with Smartpoint enabled.

    Yields the Setting overrides dict so individual tests can mutate
    per-carrier thresholds before exercising the code path.
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
    ShippingProvider.objects.filter(code__in=["acs", "boxnow"]).update(
        is_active=False
    )
    info = ShippingService.free_shipping_info()
    assert info["providers"] == []
    assert info["min_threshold"] is None
    assert info["max_threshold"] is None


def test_acs_only_emits_both_kinds(_activate_acs_only):
    info = ShippingService.free_shipping_info()
    kinds = {row["kind"] for row in info["providers"]}
    assert kinds == {
        ShippingKind.HOME_DELIVERY.value,
        ShippingKind.PICKUP_POINT.value,
    }
    assert {row["provider_code"] for row in info["providers"]} == {"acs"}


def test_acs_smartpoint_disabled_hides_pickup(_activate_acs_only):
    # Flip ``ACS_SMARTPOINT_ENABLED`` to False via the mock dict the
    # fixture yields. ``Setting.get`` is patched, so the override
    # takes effect immediately without a DB write (which would race
    # the ``_reseed_extra_settings`` autouse fixture under xdist).
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
    # ``_DEFAULT_SETTING_OVERRIDES`` already pins ACS at 40 and
    # BoxNow at 30, so the aggregation should pick 30/40.
    info = ShippingService.free_shipping_info()
    assert info["min_threshold"] == Decimal("30.00")
    assert info["max_threshold"] == Decimal("40.00")


def test_country_code_filter_excludes_unsupported(_activate_acs_only):
    provider = ShippingProvider.objects.get(code="acs")
    metadata = dict(provider.metadata or {})
    metadata["supported_countries"] = ["GR"]
    provider.metadata = metadata
    provider.save(update_fields=["metadata"])

    matching = ShippingService.free_shipping_info(country_code="GR")
    non_matching = ShippingService.free_shipping_info(country_code="DE")
    assert len(matching["providers"]) > 0
    assert non_matching["providers"] == []


def test_none_threshold_carrier_excluded(cleanup_no_threshold):
    register_provider(_NoThresholdCarrier)
    ShippingProvider.objects.create(
        code=_NoThresholdCarrier.code,
        name="No-Threshold Test Carrier",
        is_active=True,
        supports_home_delivery=True,
        supports_pickup_point=False,
    )
    info = ShippingService.free_shipping_info()
    codes = {row["provider_code"] for row in info["providers"]}
    assert _NoThresholdCarrier.code not in codes


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
