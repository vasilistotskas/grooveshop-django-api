"""Every vertical preset must be a configuration the slot validator
would accept, and seeding must never overwrite a merchant's edit."""

from __future__ import annotations

import pytest
from django.core.management import CommandError, call_command

from recommendation.enum import StrategyCode, Surface
from recommendation.models import RecommendationSlot
from recommendation.presets import (
    DEFAULT_PRESET,
    PRESETS,
    seed_recommendation_slots,
    slot_defaults,
)
from recommendation.schemas import validate_strategy_chain, validate_weights


@pytest.mark.parametrize("preset", sorted(PRESETS))
def test_every_preset_configures_every_surface_validly(preset):
    assert set(PRESETS[preset]) == set(Surface.values)
    for surface in Surface.values:
        defaults = slot_defaults(surface, preset)
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
    defaults = slot_defaults("nope")
    assert defaults["strategy_chain"] == [
        StrategyCode.CURATED,
        StrategyCode.VARIANT_GROUP,
        StrategyCode.CATEGORY,
        StrategyCode.POPULAR,
    ]


@pytest.mark.django_db
def test_seeding_is_idempotent_and_keeps_edits():
    assert seed_recommendation_slots() == len(Surface.values)
    assert seed_recommendation_slots() == 0

    slot = RecommendationSlot.objects.get(surface=Surface.PDP)
    slot.limit = 9
    slot.save()

    assert seed_recommendation_slots(DEFAULT_PRESET) == 0
    slot.refresh_from_db()
    assert slot.limit == 9


@pytest.mark.django_db
def test_backfill_command_refuses_an_unknown_schema():
    with pytest.raises(CommandError, match="No active non-public tenant"):
        call_command("backfill_recommendation_slots", schema="nope")
