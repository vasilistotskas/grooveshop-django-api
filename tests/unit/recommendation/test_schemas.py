"""Slot configuration validation — the reason a bad chain can never be
stored and surface as a KeyError on every product page."""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from recommendation.enum import StrategyCode, Surface
from recommendation.models import RecommendationSlot


def _slot(**kwargs) -> RecommendationSlot:
    defaults = {
        "surface": Surface.PDP,
        "strategy_chain": [StrategyCode.CURATED],
        "weights": {},
        "limit": 4,
        "min_fill": 2,
    }
    return RecommendationSlot(**{**defaults, **kwargs})


def _errors(slot: RecommendationSlot) -> dict[str, list[str]]:
    with pytest.raises(ValidationError) as excinfo:
        slot.full_clean(exclude=["surface"])
    return excinfo.value.message_dict


class TestStrategyChain:
    @pytest.mark.parametrize(
        "value",
        [
            "curated",
            [],
            ["curated", "telepathy"],
            [42],
            ["curated", "curated"],
        ],
    )
    def test_rejects(self, value):
        assert "strategy_chain" in _errors(_slot(strategy_chain=value))


class TestWeights:
    def test_valid_weights_are_stored_as_floats(self):
        slot = _slot(
            strategy_chain=["curated", "popular"],
            weights={"curated": 1, "popular": 0.25},
        )
        slot.full_clean(exclude=["surface"])
        assert slot.weights == {"curated": 1.0, "popular": 0.25}
        assert all(isinstance(v, float) for v in slot.weights.values())

    def test_empty_means_unweighted(self):
        slot = _slot(weights={})
        slot.full_clean(exclude=["surface"])
        assert slot.weights == {}

    @pytest.mark.parametrize(
        "value",
        [
            [0.5],
            {"curated": True},
            {"curated": "0.5"},
            {"curated": 1.5},
            {"curated": -0.1},
            {"telepathy": 0.5},
        ],
    )
    def test_rejects(self, value):
        assert "weights" in _errors(_slot(weights=value))

    def test_a_weight_outside_the_chain_is_refused(self):
        errors = _errors(_slot(weights={"popular": 0.5}))
        assert set(errors) == {"weights"}
        assert "popular" in errors["weights"][0]


def test_every_field_error_is_reported_at_once():
    errors = _errors(_slot(strategy_chain=["telepathy"], limit=2, min_fill=3))
    assert {"strategy_chain", "min_fill"} <= set(errors)
