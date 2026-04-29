"""Verify OrderService._resolve_shipping_provider maps the legacy enum
to the new (shipping_provider, shipping_kind) pair correctly for ACS.

This is the back-compat bridge that lets v1 API clients sending only
``shipping_method`` continue to work after Phase 0/1.
"""

from __future__ import annotations

import pytest

from order.services import OrderService

pytestmark = pytest.mark.django_db


def test_acs_smartpoint_method_maps_to_acs_provider_pickup_point():
    order_data = {
        "shipping_method": "acs_smartpoint",
    }
    OrderService._resolve_shipping_provider(order_data)

    provider = order_data.get("shipping_provider")
    assert provider is not None
    assert provider.code == "acs"
    assert order_data["shipping_kind"] == "pickup_point"


def test_box_now_locker_method_maps_to_boxnow_provider_pickup_point():
    order_data = {
        "shipping_method": "box_now_locker",
    }
    OrderService._resolve_shipping_provider(order_data)

    provider = order_data.get("shipping_provider")
    assert provider is not None
    assert provider.code == "boxnow"
    assert order_data["shipping_kind"] == "pickup_point"


def test_home_delivery_method_unattached_when_no_provider_active():
    """When no provider has ``is_active=True`` AND
    ``supports_home_delivery=True``, ``home_delivery`` orders stay
    unlinked — exactly the pre-Phase-0 behaviour for legacy rows."""
    order_data = {
        "shipping_method": "home_delivery",
    }
    OrderService._resolve_shipping_provider(order_data)

    # All seeded providers default to is_active=False so no auto-routing.
    assert order_data.get("shipping_provider") is None
    assert order_data["shipping_kind"] == "home_delivery"


def test_home_delivery_auto_routes_to_active_home_delivery_provider():
    """Once ops flips a home-delivery provider's ``is_active`` flag on,
    plain ``shipping_method=home_delivery`` orders auto-attach to it
    via the dynamic-routing fallback in ``_resolve_shipping_provider``.
    Lets ops add a new courier without touching order-flow code."""
    from shipping.models import ShippingProvider

    ShippingProvider.objects.filter(code="acs").update(is_active=True)

    order_data = {
        "shipping_method": "home_delivery",
    }
    OrderService._resolve_shipping_provider(order_data)

    provider = order_data.get("shipping_provider")
    assert provider is not None
    assert provider.code == "acs"
    assert order_data["shipping_kind"] == "home_delivery"


def test_explicit_provider_code_wins_over_legacy_enum():
    """When the new pair is sent, the legacy enum is ignored."""
    order_data = {
        "shipping_method": "home_delivery",
        "shipping_provider_code": "acs",
        "shipping_kind": "home_delivery",
    }
    OrderService._resolve_shipping_provider(order_data)

    provider = order_data.get("shipping_provider")
    assert provider is not None
    assert provider.code == "acs"
    assert order_data["shipping_kind"] == "home_delivery"
    # The shipping_provider_code key must be removed before
    # Order.objects.create() is called.
    assert "shipping_provider_code" not in order_data
