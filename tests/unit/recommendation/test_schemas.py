"""Slot configuration validators — the reason a bad chain can never be
stored and surface as a KeyError on every product page."""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from recommendation.enum import StrategyCode, Surface
from recommendation.models import RecommendationSlot
from recommendation.schemas import validate_strategy_chain, validate_weights


class TestStrategyChain:
    def test_valid_chain_is_returned_as_a_fresh_list(self):
        chain = [StrategyCode.CURATED, StrategyCode.POPULAR]
        out = validate_strategy_chain(chain)
        assert out == chain
        assert out is not chain

    @pytest.mark.parametrize(
        ("value", "message"),
        [
            ("curated", "must be a list"),
            ([], "at least one"),
            (["curated", "telepathy"], "not a known strategy"),
            ([42], "not a known strategy"),
            (["curated", "curated"], "more than once"),
        ],
    )
    def test_rejects(self, value, message):
        with pytest.raises(ValidationError, match=message):
            validate_strategy_chain(value)


class TestWeights:
    def test_valid_weights_are_coerced_to_floats(self):
        out = validate_weights(
            {"curated": 1, "popular": 0.25}, ["curated", "popular"]
        )
        assert out == {"curated": 1.0, "popular": 0.25}
        assert all(isinstance(v, float) for v in out.values())

    def test_empty_means_unweighted(self):
        assert validate_weights({}, ["curated"]) == {}

    @pytest.mark.parametrize(
        ("value", "message"),
        [
            ([0.5], "must be a mapping"),
            ({"popular": 0.5}, "not in the chain"),
            ({"curated": True}, "must be a number"),
            ({"curated": "0.5"}, "must be a number"),
            ({"curated": 1.5}, "between 0 and 1"),
            ({"curated": -0.1}, "between 0 and 1"),
        ],
    )
    def test_rejects(self, value, message):
        with pytest.raises(ValidationError, match=message):
            validate_weights(value, ["curated"])


class TestSlotClean:
    def test_collects_every_field_error_at_once(self):
        slot = RecommendationSlot(
            surface=Surface.PDP,
            strategy_chain=["telepathy"],
            weights={},
            limit=2,
            min_fill=3,
        )
        with pytest.raises(ValidationError) as excinfo:
            slot.clean()
        assert set(excinfo.value.message_dict) == {"strategy_chain", "min_fill"}

    def test_weights_are_checked_against_the_cleaned_chain(self):
        slot = RecommendationSlot(
            surface=Surface.PDP,
            strategy_chain=[StrategyCode.CURATED],
            weights={"popular": 0.5},
            limit=4,
            min_fill=2,
        )
        with pytest.raises(ValidationError) as excinfo:
            slot.clean()
        assert set(excinfo.value.message_dict) == {"weights"}

    def test_a_valid_slot_is_normalised_in_place(self):
        slot = RecommendationSlot(
            surface=Surface.PDP,
            strategy_chain=[StrategyCode.CURATED],
            weights={"curated": 1},
            limit=4,
            min_fill=2,
        )
        slot.clean()
        assert slot.weights == {"curated": 1.0}
