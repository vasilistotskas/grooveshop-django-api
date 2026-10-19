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


def test_the_empty_cart_refuses_strategies_that_need_a_product():
    """The empty cart has no product to start from: a chain naming one
    of those strategies would save, then show nothing from it."""
    slot = RecommendationSlot(
        surface=Surface.EMPTY_CART,
        strategy_chain=[StrategyCode.CURATED, StrategyCode.POPULAR],
        weights={},
        limit=6,
        min_fill=3,
    )
    with pytest.raises(ValidationError) as excinfo:
        slot.full_clean()
    (message,) = excinfo.value.message_dict["strategy_chain"]
    assert str(StrategyCode.CURATED.label) in message
    assert str(StrategyCode.POPULAR.label) not in message

    slot.strategy_chain = [StrategyCode.POPULAR]
    slot.full_clean()


def test_a_product_surface_accepts_every_strategy():
    RecommendationSlot(
        surface=Surface.PDP,
        strategy_chain=list(StrategyCode.values),
        weights={},
        limit=4,
        min_fill=2,
    ).full_clean()


def test_turning_weight_learning_off_is_a_valid_edit():
    seed_recommendation_slots(StoreVertical.GENERAL)
    pdp = RecommendationSlot.objects.get(surface=Surface.PDP)
    assert pdp.learn_weights is True
    pdp.learn_weights = False
    pdp.weights = {StrategyCode.CURATED: 0.5}
    pdp.full_clean()
    pdp.save()
    pdp.refresh_from_db()
    assert pdp.learn_weights is False
