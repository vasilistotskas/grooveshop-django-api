"""Unit tests for ``BoxNowCarrier.is_kind_enabled`` credential gating.

BoxNow credentials are tenant-only (no settings fallback — see
``tenant/credentials.py:box_now_credentials()``), so an unconfigured
tenant must see BoxNow as entirely unavailable, not just missing a
per-kind feature flag (BoxNow has no such flag; it only ever serves
``pickup_point``).
"""

from __future__ import annotations

import pytest

from shipping.enum import ShippingKind
from shipping.interfaces import get_provider

pytestmark = pytest.mark.django_db


def test_pickup_point_disabled_without_boxnow_credentials():
    adapter = get_provider("boxnow")
    assert adapter.is_kind_enabled(ShippingKind.PICKUP_POINT) is False


def test_home_delivery_disabled_without_boxnow_credentials():
    """BoxNow never actually serves home delivery, but the gate must
    still report False consistently (not raise) for an unsupported
    kind when unconfigured."""
    adapter = get_provider("boxnow")
    assert adapter.is_kind_enabled(ShippingKind.HOME_DELIVERY) is False


def test_pickup_point_enabled_with_boxnow_credentials(
    boxnow_configured_tenant,
):
    adapter = get_provider("boxnow")
    assert adapter.is_kind_enabled(ShippingKind.PICKUP_POINT) is True
