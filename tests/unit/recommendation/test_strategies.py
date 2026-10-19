"""Each strategy: what it answers, what it declines, and the one
property the engine's constant cost rests on — a query count that does
not grow with the seed count.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.conf import settings
from django.db import connection
from django.db.models.signals import post_save
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from factory.django import mute_signals

from order.enum.status import OrderStatus
from order.factories.item import OrderItemFactory
from order.factories.order import OrderFactory
from order.models.order import Order
from product.enum.relation import RelationType
from product.factories.attribute import AttributeFactory
from product.factories.attribute_value import AttributeValueFactory
from product.factories.brand import BrandFactory
from product.factories.category import ProductCategoryFactory
from product.factories.product import ProductFactory
from product.factories.variant_group import ProductVariantGroupFactory
from product.models import ProductAttribute, ProductRelation
from recommendation.enum import StrategyCode
from recommendation.strategies import get_strategy
from recommendation.strategies.base import TenantContext
from tag.factories.tag import TagFactory
from tag.factories.tagged_item import TaggedProductFactory
from tenant.models import TenantPlan

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

        with CaptureQueriesContext(connection) as one:
            strategy.candidates_for([seeds[0].id], 4)
        with CaptureQueriesContext(connection) as three:
            out = strategy.candidates_for([s.id for s in seeds], 4)

        assert len(one) == len(three) == 1
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

        with CaptureQueriesContext(connection) as one:
            strategy.candidates_for([seeds[0].id], 4)
        with CaptureQueriesContext(connection) as three:
            strategy.candidates_for([s.id for s in seeds], 4)

        assert len(one) == len(three) == 2


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

        with CaptureQueriesContext(connection) as one:
            strategy.candidates_for([lonely.id], 4)
        with CaptureQueriesContext(connection) as three:
            strategy.candidates_for([s.id for s in seeds], 4)

        assert len(one) == len(three) == 3


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

        with CaptureQueriesContext(connection) as one:
            strategy.candidates_for([seeds[0].id], 4)
        with CaptureQueriesContext(connection) as three:
            out = strategy.candidates_for([s.id for s in seeds], 4)

        assert len(one) == len(three) == 1
        assert set(out) == {s.id for s in seeds}


def _context(plan, **facts):
    values = {
        "product_count": 10,
        "has_brands": False,
        "has_tags": False,
        "has_attributes": False,
        "order_count": 0,
        "multi_item_order_count": 0,
    }
    values.update(facts)
    return TenantContext(plan=plan, **values)


def _value(active=True):
    return AttributeValueFactory(
        attribute=AttributeFactory(active=True), active=active
    )


def _with_values(product, *values):
    for value in values:
        ProductAttribute.objects.create(product=product, attribute_value=value)
    return product


class TestAttributes:
    def test_more_shared_features_rank_first_by_jaccard(self):
        red, cotton = _value(), _value()
        brand = BrandFactory()
        seed = _with_values(_product(brand=brand), red, cotton)
        twin = _with_values(_product(brand=brand), red, cotton)
        cousin = _with_values(_product(), red)
        _with_values(_product(), _value())

        out = get_strategy(StrategyCode.ATTRIBUTES).candidates(seed.id, 4)

        assert _ids(out) == [twin.id, cousin.id]
        # 3 shared of 3 | 1 shared of seed 3 + cousin 1 - 1
        assert [c.score for c in out] == [1.0, round(1 / 3, 4)]

    def test_tags_count_as_features(self):
        tag = TagFactory(active=True)
        seed, tagged = _product(), _product()
        TaggedProductFactory(content_object=seed, tag=tag)
        TaggedProductFactory(content_object=tagged, tag=tag)

        out = get_strategy(StrategyCode.ATTRIBUTES).candidates(seed.id, 4)

        assert _ids(out) == [tagged.id]

    def test_inactive_features_and_unsellable_products_are_ignored(self):
        hidden_value = _value(active=False)
        hidden_tag = TagFactory(active=False)
        shared = _value()
        seed = _with_values(_product(), hidden_value, shared)
        same_hidden = _with_values(_product(), hidden_value)
        TaggedProductFactory(content_object=seed, tag=hidden_tag)
        TaggedProductFactory(content_object=same_hidden, tag=hidden_tag)
        _with_values(_product(active=False), shared)

        strategy = get_strategy(StrategyCode.ATTRIBUTES)
        assert strategy.candidates(seed.id, 4) == []

    @override_settings(RECOMMENDATION_ATTRIBUTES_MAX_FEATURE_PRODUCTS=2)
    def test_a_feature_on_too_many_products_finds_no_candidates(self):
        """The brand is on three active products — over the cap — so
        it finds nobody; it still counts in each product's set size."""
        brand = BrandFactory()
        rare = _value()
        seed = _with_values(_product(brand=brand), rare)
        partner = _with_values(_product(brand=brand), rare)
        _product(brand=brand)

        out = get_strategy(StrategyCode.ATTRIBUTES).candidates(seed.id, 4)

        assert _ids(out) == [partner.id]
        # Overlap counted on the rare value only: 1 / (2 + 2 - 1).
        assert out[0].score == round(1 / 3, 4)

    def test_declines_below_its_plan_or_without_any_signal(self):
        strategy = get_strategy(StrategyCode.ATTRIBUTES)

        assert not strategy.is_available(
            _context(TenantPlan.TRIAL, has_attributes=True)
        )
        assert not strategy.is_available(_context(TenantPlan.BASIC))
        for signal in ("has_attributes", "has_tags", "has_brands"):
            assert strategy.is_available(
                _context(TenantPlan.BASIC, **{signal: True})
            )

    def test_query_count_does_not_grow_with_the_seeds(self):
        value = _value()
        seeds = [_with_values(_product(), value) for _ in range(3)]
        _with_values(_product(), value)
        strategy = get_strategy(StrategyCode.ATTRIBUTES)

        with CaptureQueriesContext(connection) as one:
            strategy.candidates_for([seeds[0].id], 4)
        with CaptureQueriesContext(connection) as three:
            out = strategy.candidates_for([s.id for s in seeds], 4)

        assert len(one) == len(three)
        assert set(out) == {s.id for s in seeds}


def _bought_together(*products, status=OrderStatus.COMPLETED):
    with mute_signals(post_save):
        order = OrderFactory(num_order_items=0, status=status)
        for product in products:
            OrderItemFactory(order=order, product=product)
    return order


class TestCoPurchase:
    @pytest.fixture(autouse=True)
    def _support_of_two(self, settings):
        settings.RECOMMENDATION_CO_PURCHASE_MIN_SUPPORT = 2

    def test_scored_by_the_share_of_the_seeds_orders(self):
        seed, partner, sometimes, once = (_product() for _ in range(4))
        for _ in range(2):
            _bought_together(seed, partner, sometimes)
        _bought_together(seed, partner, once)
        _bought_together(seed, partner)
        _bought_together(seed)

        out = get_strategy(StrategyCode.CO_PURCHASE).candidates(seed.id, 4)

        # 4 and 2 of the seed's 5 orders; ``once`` is under the support.
        assert _ids(out) == [partner.id, sometimes.id]
        assert [c.score for c in out] == [0.8, 0.4]

    def test_canceled_old_and_unsellable_purchases_do_not_count(self):
        seed, partner, retired = _product(), _product(), _product()
        for _ in range(2):
            _bought_together(seed, partner, status=OrderStatus.CANCELED)
        old = [_bought_together(seed, partner) for _ in range(2)]
        window = settings.RECOMMENDATION_CO_PURCHASE_WINDOW_DAYS
        Order.objects.filter(pk__in=[o.pk for o in old]).update(
            created_at=timezone.now() - timedelta(days=window + 1)
        )
        for _ in range(2):
            _bought_together(seed, retired)
        retired.active = False
        retired.save()

        strategy = get_strategy(StrategyCode.CO_PURCHASE)
        assert strategy.candidates(seed.id, 4) == []

    @override_settings(RECOMMENDATION_CO_PURCHASE_MIN_ORDERS=50)
    def test_declines_below_its_plan_or_its_multi_item_orders(self):
        strategy = get_strategy(StrategyCode.CO_PURCHASE)

        assert not strategy.is_available(
            _context(TenantPlan.BASIC, multi_item_order_count=500)
        )
        assert not strategy.is_available(
            _context(TenantPlan.PRO, multi_item_order_count=49)
        )
        assert strategy.is_available(
            _context(TenantPlan.PRO, multi_item_order_count=50)
        )

    def test_two_queries_for_any_number_of_seeds(self):
        seeds = [_product() for _ in range(3)]
        partner = _product()
        for seed in seeds:
            for _ in range(2):
                _bought_together(seed, partner)
        strategy = get_strategy(StrategyCode.CO_PURCHASE)

        with CaptureQueriesContext(connection) as one:
            strategy.candidates_for([seeds[0].id], 4)
        with CaptureQueriesContext(connection) as three:
            out = strategy.candidates_for([s.id for s in seeds], 4)

        assert len(one) == len(three) == 2
        assert set(out) == {s.id for s in seeds}
