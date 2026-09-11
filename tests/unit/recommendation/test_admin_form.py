"""The slot admin form: what a merchant can actually save.

``full_clean`` is what the ModelForm runs. Empty ``weights`` is the
normal, seeded state ("unweighted"), and the first production edit of a
slot (lifting webside's cart price band, 2026-09-10) was refused with
"This field cannot be blank" because the JSONField lacked ``blank``.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from recommendation.enum import StrategyCode, Surface
from recommendation.models import RecommendationSlot
from recommendation.presets import seed_recommendation_slots
from tenant.models import StoreVertical

pytestmark = pytest.mark.django_db


def test_a_seeded_slot_passes_full_clean_unchanged():
    seed_recommendation_slots(StoreVertical.GENERAL)
    for slot in RecommendationSlot.objects.all():
        assert slot.weights == {}
        slot.full_clean()


def test_lifting_the_price_band_is_a_valid_edit():
    seed_recommendation_slots(StoreVertical.GENERAL)
    cart = RecommendationSlot.objects.get(surface=Surface.CART)
    cart.price_band_ratio = None
    cart.full_clean()
    cart.save()
    cart.refresh_from_db()
    assert cart.price_band_ratio is None


def test_full_clean_still_refuses_a_bad_chain():
    slot = RecommendationSlot(
        surface=Surface.PDP,
        strategy_chain=[StrategyCode.CURATED, "telepathy"],
        weights={},
        limit=4,
        min_fill=2,
    )
    with pytest.raises(ValidationError) as excinfo:
        slot.full_clean()
    assert "strategy_chain" in excinfo.value.message_dict
