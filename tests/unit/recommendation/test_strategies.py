"""Each Free-tier strategy: what it answers, what it declines, and the
one property the engine's constant cost rests on — a fixed number of
queries whatever the seed count.
"""

from __future__ import annotations

import pytest

from product.enum.relation import RelationType
from product.factories.category import ProductCategoryFactory
from product.factories.product import ProductFactory
from product.factories.variant_group import ProductVariantGroupFactory
from product.models import ProductRelation
from recommendation.enum import StrategyCode
from recommendation.strategies import get_strategy
from tests.utils import count_queries

pytestmark = pytest.mark.django_db


def _product(**kwargs):
    kwargs.setdefault("active", True)
    kwargs.setdefault("stock", 5)
    kwargs.setdefault("num_images", 0)
    kwargs.setdefault("num_reviews", 0)
    return ProductFactory(**kwargs)


def _ids(candidates):
    return [c.product_id for c in candidates]


class TestCurated:
    def test_merchant_order_and_relation_type_survive(self):
        seed = _product()
        second = _product()
        first = _product()
        # ``SortableModel`` appends on create; the merchant then drags
        # the second relation above the first.
        older = ProductRelation.objects.create(
            from_product=seed,
            to_product=second,
            relation_type=RelationType.SIMILAR,
        )
        newer = ProductRelation.objects.create(
            from_product=seed,
            to_product=first,
            relation_type=RelationType.ACCESSORY,
        )
        ProductRelation.objects.filter(pk=older.pk).update(sort_order=1)
        ProductRelation.objects.filter(pk=newer.pk).update(sort_order=0)

        out = get_strategy(StrategyCode.CURATED).candidates(seed.id, 4)

        assert _ids(out) == [first.id, second.id]
        assert out[0].relation_type == RelationType.ACCESSORY
        assert out[0].score > out[1].score

    def test_inactive_and_deleted_targets_are_not_candidates(self):
        seed = _product()
        inactive = _product(active=False)
        deleted = _product()
        deleted.delete()  # soft delete
        ProductRelation.objects.create(from_product=seed, to_product=inactive)
        ProductRelation.objects.create(from_product=seed, to_product=deleted)

        assert get_strategy(StrategyCode.CURATED).candidates(seed.id, 4) == []

    def test_direction_matters(self):
        """A relation FROM b TO a does not make b a candidate for a."""
        a, b = _product(), _product()
        ProductRelation.objects.create(from_product=b, to_product=a)

        assert get_strategy(StrategyCode.CURATED).candidates(a.id, 4) == []

    def test_one_query_for_any_number_of_seeds(self):
        seeds = [_product() for _ in range(3)]
        for seed in seeds:
            ProductRelation.objects.create(
                from_product=seed, to_product=_product()
            )
        strategy = get_strategy(StrategyCode.CURATED)

        with count_queries() as one:
            strategy.candidates_for([seeds[0].id], 4)
        with count_queries() as three:
            out = strategy.candidates_for([s.id for s in seeds], 4)

        assert one.count == three.count == 1
        assert set(out) == {s.id for s in seeds}

    def test_limit_applies_per_seed(self):
        seed = _product()
        for _ in range(3):
            ProductRelation.objects.create(
                from_product=seed, to_product=_product()
            )

        assert (
            len(get_strategy(StrategyCode.CURATED).candidates(seed.id, 2)) == 2
        )


class TestVariantGroup:
    def test_siblings_only_never_the_seed(self):
        group = ProductVariantGroupFactory(active=True)
        seed = _product(variant_group=group)
        sibling = _product(variant_group=group)
        _product()  # unrelated

        out = get_strategy(StrategyCode.VARIANT_GROUP).candidates(seed.id, 4)

        assert _ids(out) == [sibling.id]

    def test_no_group_means_no_answer(self):
        seed = _product(variant_group=None)
        assert (
            get_strategy(StrategyCode.VARIANT_GROUP).candidates(seed.id, 4)
            == []
        )

    def test_two_queries_for_any_number_of_seeds(self):
        group = ProductVariantGroupFactory(active=True)
        seeds = [_product(variant_group=group) for _ in range(3)]
        strategy = get_strategy(StrategyCode.VARIANT_GROUP)

        with count_queries() as one:
            strategy.candidates_for([seeds[0].id], 4)
        with count_queries() as three:
            strategy.candidates_for([s.id for s in seeds], 4)

        assert one.count == three.count == 2


class TestCategory:
    @pytest.fixture
    def tree(self):
        parent = ProductCategoryFactory(active=True)
        own = ProductCategoryFactory(active=True, parent=parent)
        sibling = ProductCategoryFactory(active=True, parent=parent)
        deeper = ProductCategoryFactory(active=True, parent=own)
        elsewhere = ProductCategoryFactory(active=True)
        return {
            "own": own,
            "sibling": sibling,
            "deeper": deeper,
            "elsewhere": elsewhere,
        }

    def test_tiers_same_then_sibling_then_subtree(self, tree):
        seed = _product(category=tree["own"], view_count=0)
        same = _product(category=tree["own"], view_count=10)
        sib = _product(category=tree["sibling"], view_count=10)
        deep = _product(category=tree["deeper"], view_count=10)
        _product(category=tree["elsewhere"], view_count=10)

        out = get_strategy(StrategyCode.CATEGORY).candidates(seed.id, 6)

        assert _ids(out) == [same.id, sib.id, deep.id]
        assert [c.score for c in out] == [0.8, 0.6, 0.5]

    def test_within_a_tier_demonstrated_interest_wins(self, tree):
        seed = _product(category=tree["own"], view_count=0)
        quiet = _product(category=tree["own"], view_count=1, click_score=0)
        busy = _product(category=tree["own"], view_count=100, click_score=0)

        out = get_strategy(StrategyCode.CATEGORY).candidates(seed.id, 6)

        assert _ids(out) == [busy.id, quiet.id]

    def test_inactive_categories_and_products_are_skipped(self, tree):
        seed = _product(category=tree["own"])
        _product(category=tree["own"], active=False)
        dark = ProductCategoryFactory(active=False, parent=tree["own"].parent)
        _product(category=dark)

        assert get_strategy(StrategyCode.CATEGORY).candidates(seed.id, 6) == []

    def test_no_category_means_no_answer(self):
        seed = _product(category=None)
        assert get_strategy(StrategyCode.CATEGORY).candidates(seed.id, 4) == []

    def test_three_queries_whatever_the_seeds_and_tiers_reached(self, tree):
        lonely = _product(category=tree["elsewhere"])
        seeds = [_product(category=tree["own"]) for _ in range(3)]
        _product(category=tree["sibling"])
        strategy = get_strategy(StrategyCode.CATEGORY)

        with count_queries() as one:
            strategy.candidates_for([lonely.id], 4)
        with count_queries() as three:
            strategy.candidates_for([s.id for s in seeds], 4)

        assert one.count == three.count == 3


class TestPopular:
    def test_excludes_the_seeds_and_the_unsellable(self):
        seed = _product(view_count=1_000)
        hot = _product(view_count=500)
        _product(view_count=900, stock=0)
        _product(view_count=900, active=False)

        out = get_strategy(StrategyCode.POPULAR).candidates(seed.id, 4)

        assert _ids(out) == [hot.id]
        assert out[0].score <= 0.5

    def test_one_query_shared_by_every_seed(self):
        seeds = [_product() for _ in range(3)]
        _product()
        strategy = get_strategy(StrategyCode.POPULAR)

        with count_queries() as one:
            strategy.candidates_for([seeds[0].id], 4)
        with count_queries() as three:
            out = strategy.candidates_for([s.id for s in seeds], 4)

        assert one.count == three.count == 1
        assert set(out) == {s.id for s in seeds}
