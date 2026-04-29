"""Unit tests for the ShippingProvider model and seed migrations."""

from __future__ import annotations

import pytest

from shipping.factories import ShippingProviderFactory
from shipping.models import ShippingProvider

pytestmark = pytest.mark.django_db


def test_supports_home_delivery_when_flag_true():
    provider = ShippingProviderFactory(
        code="acs_test",
        supports_home_delivery=True,
        supports_pickup_point=False,
    )
    assert provider.supports("home_delivery") is True
    assert provider.supports("pickup_point") is False


def test_supports_unknown_kind_returns_false():
    provider = ShippingProviderFactory(code="generic_test")
    assert provider.supports("smoke_signal") is False


def test_seed_migration_inserts_acs_and_boxnow():
    # Both rows are created by shipping/migrations/0002_seed_providers.
    codes = set(ShippingProvider.objects.values_list("code", flat=True))
    assert {"acs", "boxnow"}.issubset(codes)


def test_seed_migration_acs_supports_both_kinds():
    """Phase 2 migration flips ``supports_pickup_point`` to True so
    ACS can host both home delivery and Smartpoint pickups.

    The customer-facing locker UX is gated behind
    ``ACS_SMARTPOINT_ENABLED`` (a Setting), not the capability flag.
    """
    acs = ShippingProvider.objects.get(code="acs")
    assert acs.supports_home_delivery is True
    assert acs.supports_pickup_point is True
    assert acs.is_active is False  # default off
    assert acs.metadata.get("locker_picker_kind") == "acs_db_picker"


def test_seed_migration_boxnow_supports_pickup_point_only():
    boxnow = ShippingProvider.objects.get(code="boxnow")
    assert boxnow.supports_home_delivery is False
    assert boxnow.supports_pickup_point is True
    assert boxnow.metadata.get("locker_picker_kind") == "boxnow_widget"
