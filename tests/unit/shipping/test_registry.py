"""Unit tests for the shipping carrier registry."""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from shipping.enum import ShippingKind
from shipping.exceptions import ShippingProviderNotFoundError
from shipping.interfaces import (
    ShippingCarrierInterface,
    _REGISTRY,
    get_provider,
    is_registered,
    register_provider,
    registered_codes,
)


class _DummyCarrier(ShippingCarrierInterface):
    """Concrete adapter used to exercise the registry."""

    code: ClassVar[str] = "dummy_test"

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
def cleanup_dummy():
    yield
    _REGISTRY.pop(_DummyCarrier.code, None)


def test_register_provider_indexes_by_code(cleanup_dummy):
    register_provider(_DummyCarrier)
    assert is_registered("dummy_test") is True
    adapter = get_provider("dummy_test")
    assert isinstance(adapter, _DummyCarrier)


def test_register_provider_overwrites_same_code(cleanup_dummy):
    register_provider(_DummyCarrier)
    register_provider(_DummyCarrier)  # second call is fine
    assert _REGISTRY["dummy_test"].__class__ is _DummyCarrier


def test_get_provider_raises_for_missing_code():
    with pytest.raises(ShippingProviderNotFoundError) as exc_info:
        get_provider("does-not-exist")
    assert exc_info.value.code == "does-not-exist"


def test_register_provider_rejects_empty_code():
    class _NoCode(_DummyCarrier):
        code = ""

    with pytest.raises(ValueError):
        register_provider(_NoCode)


def test_registered_codes_contains_boxnow():
    # BoxNow registers itself in AppConfig.ready(). Sanity check that
    # the live test environment sees the boxnow adapter.
    assert "boxnow" in registered_codes()


def test_default_calculate_shipping_cost_returns_none(cleanup_dummy):
    register_provider(_DummyCarrier)
    adapter = get_provider("dummy_test")
    result = adapter.calculate_shipping_cost(
        order_value_amount=42.0,
        currency="EUR",
        kind=ShippingKind.HOME_DELIVERY,
    )
    assert result is None
