"""Provisioning seeds a store's recommendation slots from ITS vertical —
the platform sets ``Tenant.vertical`` at onboarding and the engine's
defaults follow it, with no second step."""

from __future__ import annotations

import pytest

from recommendation.enum import Surface
from recommendation.models import RecommendationSlot
from recommendation.presets import PRESETS
from tenant.models import StoreVertical
from tenant.provisioning import _seed_recommendation_slots

pytestmark = pytest.mark.django_db


def test_slots_follow_the_tenant_vertical(tenant_factory):
    tenant = tenant_factory("vertical-plants-tenant")
    tenant.vertical = StoreVertical.PLANTS_GARDEN
    tenant.save(update_fields=["vertical"])

    assert _seed_recommendation_slots(tenant) is True

    cart = RecommendationSlot.objects.get(surface=Surface.CART)
    expected = PRESETS[StoreVertical.PLANTS_GARDEN][Surface.CART]
    assert cart.strategy_chain == expected["strategy_chain"]
    assert str(cart.price_band_ratio) == expected["price_band_ratio"]


def test_a_new_tenant_defaults_to_general(tenant_factory):
    tenant = tenant_factory("vertical-default-tenant")
    assert tenant.vertical == StoreVertical.GENERAL
