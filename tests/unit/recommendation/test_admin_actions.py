"""The slot admin's "Reset to preset" actions.

They read the STORE's vertical (``Tenant.vertical``) — the same preset
provisioning seeded — so a merchant who broke a chain gets back exactly
what they started with, on one surface or all of them. Dialog actions
post ``_form_submitted``; without it the wrapper only opens the dialog.
"""

from __future__ import annotations

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory

from recommendation.admin import RecommendationSlotAdmin
from recommendation.enum import StrategyCode, Surface
from recommendation.models import RecommendationSlot
from recommendation.presets import PRESETS, seed_recommendation_slots
from tenant.models import StoreVertical
from tests.utils.staff import (
    bind_store_tenant,
    store_tenant,
    unbind_store_tenant,
)
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db


def _post(staff_user):
    request = RequestFactory().post(
        "/admin/recommendation/", {"_form_submitted": "true"}
    )
    request.user = staff_user
    request.session = {}
    request._messages = FallbackStorage(request)
    return request


@pytest.fixture
def staff_user():
    return UserAccountFactory(is_staff=True, is_superuser=True)


@pytest.fixture
def slot_admin():
    return RecommendationSlotAdmin(RecommendationSlot, AdminSite())


@pytest.fixture
def fashion_store():
    tenant = store_tenant("recs_reset_tenant", vertical=StoreVertical.FASHION)
    previous = bind_store_tenant(tenant)
    yield tenant
    unbind_store_tenant(previous)


def _break(slot):
    slot.strategy_chain = [StrategyCode.POPULAR]
    slot.limit = 1
    slot.min_fill = 1
    slot.enabled = False
    slot.save()


def test_reset_one_slot_restores_the_store_vertical_preset(
    slot_admin, staff_user, fashion_store
):
    seed_recommendation_slots(StoreVertical.GENERAL)
    pdp = RecommendationSlot.objects.get(surface=Surface.PDP)
    cart = RecommendationSlot.objects.get(surface=Surface.CART)
    _break(pdp)
    _break(cart)

    response = slot_admin.reset_to_preset(_post(staff_user), object_id=pdp.pk)

    assert response.status_code == 200
    assert response.headers["HX-Redirect"].endswith(f"/{pdp.pk}/change/")
    pdp.refresh_from_db()
    expected = PRESETS[StoreVertical.FASHION][Surface.PDP]
    assert pdp.strategy_chain == expected["strategy_chain"]
    assert pdp.enabled is True
    # The other slot is untouched.
    cart.refresh_from_db()
    assert cart.strategy_chain == [StrategyCode.POPULAR]


def test_reset_all_restores_every_surface_and_creates_missing_ones(
    slot_admin, staff_user, fashion_store
):
    seed_recommendation_slots(StoreVertical.GENERAL)
    for slot in RecommendationSlot.objects.all():
        _break(slot)
    RecommendationSlot.objects.filter(surface=Surface.EMPTY_CART).delete()

    response = slot_admin.reset_all_to_preset(_post(staff_user))

    assert response.status_code == 200
    assert response.headers["HX-Redirect"].endswith("/recommendationslot/")
    assert RecommendationSlot.objects.count() == len(Surface.values)
    for slot in RecommendationSlot.objects.all():
        expected = PRESETS[StoreVertical.FASHION][slot.surface]
        assert slot.strategy_chain == expected["strategy_chain"], slot.surface
        assert slot.enabled is True


def test_without_a_tenant_the_general_preset_applies(slot_admin, staff_user):
    seed_recommendation_slots(StoreVertical.FASHION)
    pdp = RecommendationSlot.objects.get(surface=Surface.PDP)

    slot_admin.reset_to_preset(_post(staff_user), object_id=pdp.pk)

    pdp.refresh_from_db()
    assert (
        pdp.strategy_chain
        == PRESETS[StoreVertical.GENERAL][Surface.PDP]["strategy_chain"]
    )
