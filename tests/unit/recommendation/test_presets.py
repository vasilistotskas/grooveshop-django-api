"""Every vertical has a preset the slot validator would accept, seeding
never overwrites a merchant's edit, and the explicit reset does."""

from __future__ import annotations

import pytest
from django.core.management import CommandError, call_command

from recommendation.enum import StrategyCode, Surface
from recommendation.models import RecommendationSlot
from recommendation.presets import (
    PRESETS,
    reset_slot_to_preset,
    seed_recommendation_slots,
    slot_defaults,
)
from recommendation.schemas import validate_strategy_chain, validate_weights
from tenant.models import StoreVertical


def test_presets_and_verticals_are_the_same_set():
    """``Tenant.vertical`` is the key: a vertical without a preset would
    seed nothing, a preset without a vertical could never be chosen."""
    assert set(PRESETS) == set(StoreVertical.values)


@pytest.mark.parametrize("vertical", sorted(StoreVertical.values))
def test_every_preset_configures_every_surface_validly(vertical):
    assert set(PRESETS[vertical]) == set(Surface.values)
    for surface in Surface.values:
        defaults = slot_defaults(surface, vertical)
        chain = validate_strategy_chain(defaults["strategy_chain"])
        validate_weights(defaults["weights"], chain)
        assert defaults["min_fill"] <= defaults["limit"]
        RecommendationSlot(surface=surface, **defaults).clean()


def test_curated_leads_or_follows_only_variant_group_on_every_pdp():
    """The Free tier IS curated relations; no vertical demotes them
    below an inferred strategy on the product page."""
    for preset in PRESETS.values():
        chain = preset[Surface.PDP]["strategy_chain"]
        assert chain.index(StrategyCode.CURATED) <= 1, chain


def test_unknown_surface_gets_the_free_chain():
    defaults = slot_defaults("nope", StoreVertical.GENERAL)
    assert defaults["strategy_chain"] == [
        StrategyCode.CURATED,
        StrategyCode.VARIANT_GROUP,
        StrategyCode.CATEGORY,
        StrategyCode.POPULAR,
    ]


@pytest.mark.django_db
def test_seeding_is_idempotent_and_keeps_edits():
    assert seed_recommendation_slots(StoreVertical.GENERAL) == len(
        Surface.values
    )
    assert seed_recommendation_slots(StoreVertical.GENERAL) == 0

    slot = RecommendationSlot.objects.get(surface=Surface.PDP)
    slot.limit = 9
    slot.save()

    assert seed_recommendation_slots(StoreVertical.FASHION) == 0
    slot.refresh_from_db()
    assert slot.limit == 9


@pytest.mark.django_db
def test_seeding_uses_the_vertical_it_is_given():
    seed_recommendation_slots(StoreVertical.FASHION)
    pdp = RecommendationSlot.objects.get(surface=Surface.PDP)
    assert (
        pdp.strategy_chain
        == PRESETS[StoreVertical.FASHION][Surface.PDP]["strategy_chain"]
    )
    assert pdp.strategy_chain[0] == StrategyCode.VARIANT_GROUP


@pytest.mark.django_db
def test_reset_overwrites_every_preset_field_and_nothing_else():
    seed_recommendation_slots(StoreVertical.GENERAL)
    cart = RecommendationSlot.objects.get(surface=Surface.CART)
    cart.limit = 9
    cart.min_fill = 3
    cart.price_band_ratio = None
    cart.enabled = False
    cart.weights = {StrategyCode.CURATED: 0.5}
    cart.strategy_chain = [StrategyCode.CURATED]
    cart.save()
    created_at = cart.created_at

    reset_slot_to_preset(cart, StoreVertical.FOOD)

    cart.refresh_from_db()
    expected = PRESETS[StoreVertical.FOOD][Surface.CART]
    assert cart.strategy_chain == expected["strategy_chain"]
    assert cart.weights == expected["weights"]
    assert cart.limit == expected["limit"] == 6
    assert cart.min_fill == expected["min_fill"]
    assert str(cart.price_band_ratio) == expected["price_band_ratio"]
    assert cart.enabled is True
    assert cart.surface == Surface.CART
    assert cart.created_at == created_at


@pytest.mark.django_db
def test_backfill_command_refuses_an_unknown_schema():
    with pytest.raises(CommandError, match="No active non-public tenant"):
        call_command("backfill_recommendation_slots", schema="nope")
