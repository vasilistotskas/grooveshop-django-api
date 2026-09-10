"""The strategy registry and the plan gate every strategy inherits."""

from __future__ import annotations

import pytest

from recommendation.enum import StrategyCode
from recommendation.strategies import base
from recommendation.strategies.base import (
    Candidate,
    RecommendationStrategy,
    TenantContext,
    get_strategy,
    iter_strategies,
    plan_allows,
    register_strategy,
)
from tenant.models import TenantPlan


def _context(plan: str) -> TenantContext:
    return TenantContext(
        plan=plan,
        product_count=10,
        has_brands=False,
        has_tags=False,
        has_attributes=False,
        order_count=0,
        multi_item_order_count=0,
        semantic_available=False,
    )


@pytest.fixture
def registry_snapshot():
    saved = dict(base._REGISTRY)
    yield
    base._REGISTRY.clear()
    base._REGISTRY.update(saved)


class TestRegistry:
    def test_step_one_strategies_are_registered_at_boot(self):
        codes = {s.code for s in iter_strategies()}
        assert {
            StrategyCode.CURATED,
            StrategyCode.VARIANT_GROUP,
            StrategyCode.CATEGORY,
            StrategyCode.POPULAR,
        } <= codes

    def test_unknown_code_raises_and_names_the_known_ones(self):
        with pytest.raises(KeyError, match="curated"):
            get_strategy("nope")

    def test_registering_without_a_code_is_refused(self, registry_snapshot):
        class Nameless(RecommendationStrategy):
            def candidates(self, product_id, limit):
                return []

        with pytest.raises(ValueError, match="non-empty class-level 'code'"):
            register_strategy(Nameless)

    def test_re_registering_a_code_swaps_the_instance(self, registry_snapshot):
        @register_strategy
        class Stub(RecommendationStrategy):
            code = StrategyCode.CO_VIEW
            label = "stub"

            def candidates(self, product_id, limit):
                return [Candidate(product_id=1, score=1.0)]

        assert isinstance(get_strategy(StrategyCode.CO_VIEW), Stub)


class TestPlanGate:
    @pytest.mark.parametrize(
        ("plan", "min_plan", "allowed"),
        [
            (TenantPlan.TRIAL, TenantPlan.TRIAL, True),
            (TenantPlan.TRIAL, TenantPlan.BASIC, False),
            (TenantPlan.BASIC, TenantPlan.TRIAL, True),
            (TenantPlan.PRO, TenantPlan.BASIC, True),
            (TenantPlan.PRO, TenantPlan.ENTERPRISE, False),
            (TenantPlan.ENTERPRISE, TenantPlan.PRO, True),
        ],
    )
    def test_plan_allows_is_a_rank_not_an_equality(
        self, plan, min_plan, allowed
    ):
        assert plan_allows(plan, min_plan) is allowed

    def test_an_unknown_plan_degrades_to_the_free_tier(self):
        """A typo in a Tenant row must neither unlock everything nor
        blank every strip on the store."""
        assert plan_allows("platinum", TenantPlan.TRIAL) is True
        assert plan_allows("platinum", TenantPlan.BASIC) is False
        assert plan_allows("platinum", TenantPlan.PRO) is False

    def test_is_available_declines_below_min_plan(self, registry_snapshot):
        @register_strategy
        class ProOnly(RecommendationStrategy):
            code = StrategyCode.CO_PURCHASE
            label = "pro only"
            min_plan = TenantPlan.PRO

            def candidates(self, product_id, limit):
                return []

        strategy = get_strategy(StrategyCode.CO_PURCHASE)
        assert strategy.is_available(_context(TenantPlan.BASIC)) is False
        assert strategy.is_available(_context(TenantPlan.PRO)) is True

    def test_default_candidates_for_loops_candidates(self, registry_snapshot):
        @register_strategy
        class PerSeed(RecommendationStrategy):
            code = StrategyCode.CO_PURCHASE
            label = "per seed"

            def candidates(self, product_id, limit):
                return [Candidate(product_id=product_id + 100, score=0.5)]

        out = get_strategy(StrategyCode.CO_PURCHASE).candidates_for([1, 2], 4)
        assert out == {
            1: [Candidate(product_id=101, score=0.5)],
            2: [Candidate(product_id=102, score=0.5)],
        }
