"""The online ranker: merge, guard, weight, diversify, cut.

Strategies are stubbed under the codes Step 1 leaves unregistered
(``co_purchase``, ``co_view``) so each test controls exactly what the
engine is handed; the real ``curated`` and ``popular`` strategies are
used where the test is about how the engine treats THEM.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest
from djmoney.money import Money

from product.enum.relation import RelationType
from product.factories.category import ProductCategoryFactory
from product.factories.product import ProductFactory
from product.models import ProductRelation
from recommendation.engine import Suggestion, SuggestionContext, suggest
from recommendation.enum import StrategyCode, Surface
from recommendation.models import RecommendationCandidate, RecommendationSlot
from recommendation.strategies import base
from recommendation.strategies.base import (
    Candidate,
    RecommendationStrategy,
    TenantContext,
    register_strategy,
)
from tenant.models import TenantPlan

pytestmark = pytest.mark.django_db


def _product(**kwargs):
    kwargs.setdefault("active", True)
    kwargs.setdefault("stock", 5)
    kwargs.setdefault("num_images", 0)
    kwargs.setdefault("num_reviews", 0)
    kwargs.setdefault("price", Money(Decimal("100.00"), "EUR"))
    kwargs.setdefault("discount_percent", Decimal(0))
    return ProductFactory(**kwargs)


def _slot(surface=Surface.PDP, chain=(), **kwargs):
    kwargs.setdefault("limit", 4)
    kwargs.setdefault("min_fill", 1)
    return RecommendationSlot.objects.create(
        surface=surface, strategy_chain=[str(c) for c in chain], **kwargs
    )


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
def stub():
    """Register a stub strategy under a spare code for one test."""
    saved = dict(base._REGISTRY)

    def _register(
        code, answers, *, precompute=False, min_plan=TenantPlan.TRIAL
    ):
        class Stub(RecommendationStrategy):
            def candidates(self, product_id, limit):
                return list(self.answers.get(product_id, []))[:limit]

        Stub.code = code
        Stub.label = f"stub {code}"
        Stub.precompute = precompute
        Stub.min_plan = min_plan
        Stub.answers = answers
        register_strategy(Stub)
        return Stub

    yield _register
    base._REGISTRY.clear()
    base._REGISTRY.update(saved)


def _ids(suggestions: list[Suggestion]) -> list[int]:
    return [s.product_id for s in suggestions]


def _suggest(seed_ids, surface=Surface.PDP, **kwargs):
    return suggest(
        SuggestionContext(surface=surface, seed_ids=tuple(seed_ids), **kwargs)
    )


class TestGuard:
    def test_below_min_fill_returns_nothing(self, stub):
        seed, only = _product(), _product()
        stub(StrategyCode.CO_PURCHASE, {seed.id: [Candidate(only.id, 0.9)]})
        _slot(chain=[StrategyCode.CO_PURCHASE], limit=4, min_fill=2)

        assert _suggest([seed.id]) == []

    def test_unsellable_seed_and_excluded_never_pass(self, stub):
        seed = _product()
        good = _product()
        gone = _product(stock=0)
        off = _product(active=False)
        banned = _product()
        stub(
            StrategyCode.CO_PURCHASE,
            {
                seed.id: [
                    Candidate(gone.id, 1.0),
                    Candidate(off.id, 1.0),
                    Candidate(seed.id, 1.0),
                    Candidate(banned.id, 1.0),
                    Candidate(good.id, 0.5),
                ]
            },
        )
        _slot(chain=[StrategyCode.CO_PURCHASE])

        assert _ids(_suggest([seed.id], exclude_ids=(banned.id,))) == [good.id]

    def test_price_band_is_relative_to_the_seed(self, stub):
        seed = _product(price=Money(Decimal("100.00"), "EUR"))
        near = _product(price=Money(Decimal("150.00"), "EUR"))
        too_dear = _product(price=Money(Decimal("250.00"), "EUR"))
        too_cheap = _product(price=Money(Decimal("40.00"), "EUR"))
        stub(
            StrategyCode.CO_PURCHASE,
            {
                seed.id: [
                    Candidate(too_dear.id, 1.0),
                    Candidate(too_cheap.id, 0.9),
                    Candidate(near.id, 0.5),
                ]
            },
        )
        _slot(
            chain=[StrategyCode.CO_PURCHASE], price_band_ratio=Decimal("2.00")
        )

        assert _ids(_suggest([seed.id])) == [near.id]

    def test_price_band_uses_the_discounted_price(self, stub):
        seed = _product(price=Money(Decimal("100.00"), "EUR"))
        # 300 list, 50% off -> 150 final: inside a 2x band only when
        # the discount is applied.
        on_sale = _product(
            price=Money(Decimal("300.00"), "EUR"),
            discount_percent=Decimal(50),
        )
        stub(StrategyCode.CO_PURCHASE, {seed.id: [Candidate(on_sale.id, 1.0)]})
        _slot(
            chain=[StrategyCode.CO_PURCHASE], price_band_ratio=Decimal("2.00")
        )

        assert _ids(_suggest([seed.id])) == [on_sale.id]


class TestRanking:
    def test_slot_weights_scale_strategy_scores(self, stub):
        seed, a, b = _product(), _product(), _product()
        stub(StrategyCode.CO_PURCHASE, {seed.id: [Candidate(a.id, 0.9)]})
        stub(StrategyCode.CO_VIEW, {seed.id: [Candidate(b.id, 0.5)]})
        slot = _slot(chain=[StrategyCode.CO_PURCHASE, StrategyCode.CO_VIEW])

        assert _ids(_suggest([seed.id])) == [a.id, b.id]

        slot.weights = {StrategyCode.CO_PURCHASE: 0.1}
        slot.save()
        assert _ids(_suggest([seed.id])) == [b.id, a.id]

    def test_a_candidate_hit_by_two_seeds_earns_the_basket_bonus(self, stub):
        s1, s2, shared, strong = _product(), _product(), _product(), _product()
        stub(
            StrategyCode.CO_PURCHASE,
            {
                s1.id: [Candidate(shared.id, 0.4), Candidate(strong.id, 0.9)],
                s2.id: [Candidate(shared.id, 0.4)],
            },
        )
        _slot(surface=Surface.CART, chain=[StrategyCode.CO_PURCHASE])

        # 0.4 + 0.4 + bonus beats 0.9; without the bonus it would not.
        out = _suggest([s1.id, s2.id], surface=Surface.CART)
        assert _ids(out)[0] == shared.id

    def test_reason_is_the_strongest_single_contribution(self, stub):
        seed, both = _product(), _product()
        ProductRelation.objects.create(
            from_product=seed,
            to_product=both,
            relation_type=RelationType.COMPLEMENTARY,
        )
        stub(StrategyCode.CO_PURCHASE, {seed.id: [Candidate(both.id, 0.5)]})
        _slot(chain=[StrategyCode.CO_PURCHASE, StrategyCode.CURATED])

        (only,) = _suggest([seed.id])
        assert only.strategy == StrategyCode.CURATED
        assert only.relation_type == RelationType.COMPLEMENTARY
        assert only.score == pytest.approx(1.5)


class TestDiversity:
    def test_popular_fills_at_most_one_slot(self):
        seed = _product()
        for _ in range(4):
            _product()
        _slot(chain=[StrategyCode.POPULAR], limit=4)

        out = _suggest([seed.id])
        assert len(out) == 1
        assert out[0].strategy == StrategyCode.POPULAR

    def test_one_category_cannot_fill_a_cart_strip(self, stub):
        category = ProductCategoryFactory(active=True)
        seed = _product(category=category)
        same = [_product(category=category) for _ in range(4)]
        stub(
            StrategyCode.CO_PURCHASE,
            {seed.id: [Candidate(p.id, 0.9) for p in same]},
        )
        _slot(surface=Surface.CART, chain=[StrategyCode.CO_PURCHASE], limit=4)
        _slot(surface=Surface.PDP, chain=[StrategyCode.CO_PURCHASE], limit=4)

        assert len(_suggest([seed.id], surface=Surface.CART)) == 3
        # The product page IS the category; the cap does not apply.
        assert len(_suggest([seed.id], surface=Surface.PDP)) == 4


class TestSlotResolution:
    def test_disabled_slot_returns_nothing(self, stub):
        seed, other = _product(), _product()
        stub(StrategyCode.CO_PURCHASE, {seed.id: [Candidate(other.id, 0.9)]})
        _slot(chain=[StrategyCode.CO_PURCHASE], enabled=False)

        assert _suggest([seed.id]) == []

    def test_missing_slot_falls_back_to_the_preset(self):
        """No row, a chain naming strategies that are not registered
        yet — the preset still answers with what IS registered."""
        seed = _product()
        curated = _product()
        ProductRelation.objects.create(from_product=seed, to_product=curated)
        _product()  # so min_fill=2 can be met via popular
        assert not RecommendationSlot.objects.exists()

        out = _suggest([seed.id])

        assert _ids(out)[0] == curated.id
        assert out[0].strategy == StrategyCode.CURATED

    def test_no_seeds_no_answer(self):
        assert _suggest([]) == []

    def test_explicit_limit_overrides_the_slot(self, stub):
        seed = _product()
        many = [_product() for _ in range(4)]
        stub(
            StrategyCode.CO_PURCHASE,
            {seed.id: [Candidate(p.id, 0.9) for p in many]},
        )
        _slot(chain=[StrategyCode.CO_PURCHASE], limit=4)

        assert len(_suggest([seed.id], limit=2)) == 2


class TestAvailability:
    def test_a_strategy_the_plan_does_not_allow_is_never_asked(self, stub):
        seed, other = _product(), _product()
        stub(
            StrategyCode.CO_PURCHASE,
            {seed.id: [Candidate(other.id, 0.9)]},
            min_plan=TenantPlan.PRO,
        )
        _slot(chain=[StrategyCode.CO_PURCHASE])

        with patch(
            "recommendation.engine.tenant_context",
            return_value=_context(TenantPlan.BASIC),
        ):
            assert _suggest([seed.id]) == []
        with patch(
            "recommendation.engine.tenant_context",
            return_value=_context(TenantPlan.PRO),
        ):
            assert _ids(_suggest([seed.id])) == [other.id]


class TestPrecomputed:
    def test_table_is_the_source_and_is_cold_filled_on_first_read(self, stub):
        seed, first, second = _product(), _product(), _product()
        answers = {seed.id: [Candidate(first.id, 0.9)]}
        stub(StrategyCode.CO_PURCHASE, answers, precompute=True)
        _slot(chain=[StrategyCode.CO_PURCHASE])
        assert not RecommendationCandidate.objects.exists()

        assert _ids(_suggest([seed.id])) == [first.id]
        row = RecommendationCandidate.objects.get(product=seed)
        assert row.candidate_id == first.id
        assert row.strategy == StrategyCode.CO_PURCHASE

        # The strategy now says something else; the table still holds
        # the computed answer until the next rebuild.
        answers[seed.id] = [Candidate(second.id, 0.9)]
        assert _ids(_suggest([seed.id])) == [first.id]
