"""Verify OrderService._resolve_shipping_provider routes the explicit
``(shipping_provider_code, shipping_kind)`` pair through to the FK +
kind columns correctly, and that the dynamic home-delivery auto-router
picks the right active provider.
"""

from __future__ import annotations

import pytest

from order.services import OrderService

pytestmark = pytest.mark.django_db


def test_explicit_provider_code_attaches_provider_fk():
    order_data = {
        "shipping_provider_code": "acs",
        "shipping_kind": "pickup_point",
    }
    OrderService._resolve_shipping_provider(order_data)

    provider = order_data.get("shipping_provider")
    assert provider is not None
    assert provider.code == "acs"
    assert order_data["shipping_kind"] == "pickup_point"
    # The shipping_provider_code key must be removed before
    # Order.objects.create() is called.
    assert "shipping_provider_code" not in order_data


def test_explicit_boxnow_pickup_point_attaches_boxnow():
    order_data = {
        "shipping_provider_code": "boxnow",
        "shipping_kind": "pickup_point",
    }
    OrderService._resolve_shipping_provider(order_data)

    provider = order_data.get("shipping_provider")
    assert provider is not None
    assert provider.code == "boxnow"
    assert order_data["shipping_kind"] == "pickup_point"


def test_home_delivery_without_explicit_provider_stays_unlinked_when_no_provider_active():
    """When no provider has ``is_active=True`` AND
    ``supports_home_delivery=True``, ``home_delivery`` orders stay
    unlinked — they fall through to the platform's flat-rate
    home-delivery path without a courier adapter."""
    order_data = {"shipping_kind": "home_delivery"}
    OrderService._resolve_shipping_provider(order_data)

    # All seeded providers default to is_active=False so no auto-routing.
    assert order_data.get("shipping_provider") is None
    assert order_data["shipping_kind"] == "home_delivery"


def test_home_delivery_auto_routes_to_active_provider():
    """Once ops flips a home-delivery provider's ``is_active`` flag on,
    plain ``home_delivery`` orders without an explicit provider code
    auto-attach to it via the dynamic-routing fallback. Lets ops add
    a new courier (ELTA / Speedex) without touching order-flow code."""
    from shipping.models import ShippingProvider

    ShippingProvider.objects.filter(code="acs").update(is_active=True)

    order_data = {"shipping_kind": "home_delivery"}
    OrderService._resolve_shipping_provider(order_data)

    provider = order_data.get("shipping_provider")
    assert provider is not None
    assert provider.code == "acs"
    assert order_data["shipping_kind"] == "home_delivery"


def test_default_kind_is_home_delivery_when_omitted():
    """Callers that send ``shipping_provider_code`` without a kind
    default to ``home_delivery`` — keeps backwards-compat with v1
    clients that only knew about a single home-delivery flow."""
    from shipping.models import ShippingProvider

    ShippingProvider.objects.filter(code="acs").update(is_active=True)

    order_data: dict = {}
    OrderService._resolve_shipping_provider(order_data)

    assert order_data["shipping_kind"] == "home_delivery"
