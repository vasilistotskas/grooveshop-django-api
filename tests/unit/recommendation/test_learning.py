"""Nightly weight learning: each learning slot's strategies re-weighted
by attach rate — of the products a strategy showed on that surface, the
share later bought — from the impression and attach rows the storefront
and the order pipeline record.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.conf import settings as django_settings
from django.utils import timezone

from product.factories.product import ProductFactory
from recommendation.enum import EventKind, StrategyCode, Surface
from recommendation.learning import learn_weights, learned_weights
from recommendation.models import RecommendationEvent, RecommendationSlot
from recommendation.tasks import learn_slot_weights
from tests.utils.store_settings import switch_store_settings

C = StrategyCode.CURATED
CAT = StrategyCode.CATEGORY
POP = StrategyCode.POPULAR


@pytest.fixture(autouse=True)
def _thresholds(settings):
    settings.RECOMMENDATION_LEARNING_MIN_IMPRESSIONS = 100
    settings.RECOMMENDATION_LEARNING_WEIGHT_FLOOR = 0.05


class TestTheRule:
    def test_the_best_rate_weighs_one_and_the_others_scale_to_it(self):
        weights = learned_weights(
            [C, CAT], {}, {C: 200, CAT: 400}, {C: 20, CAT: 10}
        )
        # 10% and 2.5%
        assert weights == {C: 1.0, CAT: 0.25}

    def test_a_strategy_that_sold_nothing_keeps_the_floor(self):
        weights = learned_weights([C, CAT], {}, {C: 200, CAT: 200}, {C: 20})
        assert weights == {C: 1.0, CAT: 0.05}

    def test_too_few_impressions_keep_the_weight_it_has(self):
        weights = learned_weights(
            [C, CAT, POP],
            {POP: 0.7},
            {C: 200, CAT: 200, POP: 99},
            {C: 20, CAT: 10, POP: 99},
        )
        assert weights == {C: 1.0, CAT: 0.5, POP: 0.7}

    def test_no_purchase_at_all_is_no_evidence(self):
        current = {C: 0.4}
        assert learned_weights([C, CAT], current, {C: 500, CAT: 500}, {}) == (
            current
        )

    def test_a_weight_for_a_strategy_out_of_the_chain_is_dropped(self):
        weights = learned_weights(
            [C, CAT], {POP: 0.3}, {C: 200, CAT: 200}, {C: 20, CAT: 20}
        )
        assert weights == {C: 1.0, CAT: 1.0}


def _record(product, surface, strategy, kind, n, *, days_ago=0):
    created = timezone.now() - timedelta(days=days_ago)
    events = RecommendationEvent.objects.bulk_create(
        RecommendationEvent(
            surface=surface,
            strategy=strategy,
            kind=kind,
            product=product,
            impression_id=uuid.uuid4(),
        )
        for _ in range(n)
    )
    # ``auto_now_add`` wins over a value passed to the constructor.
    RecommendationEvent.objects.filter(
        pk__in=[event.pk for event in events]
    ).update(created_at=created)


def _slot(surface, *, learn=True):
    return RecommendationSlot.objects.create(
        surface=surface,
        strategy_chain=[C, CAT],
        weights={},
        limit=4,
        min_fill=1,
        learn_weights=learn,
    )


@pytest.mark.django_db
class TestTheNightlyPass:
    @pytest.fixture
    def product(self):
        return ProductFactory(num_images=0, num_reviews=0)

    def _evidence(self, product, surface, *, days_ago=0):
        _record(
            product, surface, C, EventKind.IMPRESSION, 100, days_ago=days_ago
        )
        _record(product, surface, C, EventKind.ATTACH, 10, days_ago=days_ago)
        _record(
            product, surface, CAT, EventKind.IMPRESSION, 100, days_ago=days_ago
        )
        _record(product, surface, CAT, EventKind.ATTACH, 5, days_ago=days_ago)

    def test_each_slot_learns_from_its_own_surface(self, product):
        pdp, cart = _slot(Surface.PDP), _slot(Surface.CART)
        self._evidence(product, Surface.PDP)
        # The cart saw the opposite: category sold, curated did not.
        _record(product, Surface.CART, C, EventKind.IMPRESSION, 100)
        _record(product, Surface.CART, CAT, EventKind.IMPRESSION, 100)
        _record(product, Surface.CART, CAT, EventKind.ATTACH, 10)

        assert learn_weights() == 2

        pdp.refresh_from_db()
        cart.refresh_from_db()
        assert pdp.weights == {C: 1.0, CAT: 0.5}
        assert cart.weights == {C: 0.05, CAT: 1.0}

    def test_a_slot_with_hand_set_weights_is_left_alone(self, product):
        manual = _slot(Surface.PDP, learn=False)
        manual.weights = {C: 0.2}
        manual.save()
        self._evidence(product, Surface.PDP)

        assert learn_weights() == 0
        manual.refresh_from_db()
        assert manual.weights == {C: 0.2}

    def test_events_older_than_the_window_do_not_count(self, product):
        pdp = _slot(Surface.PDP)
        window = django_settings.RECOMMENDATION_LEARNING_WINDOW_DAYS
        self._evidence(product, Surface.PDP, days_ago=window + 1)

        assert learn_weights() == 0
        pdp.refresh_from_db()
        assert pdp.weights == {}

    def test_the_task_learns_only_where_the_store_shows_suggestions(
        self, product
    ):
        pdp = _slot(Surface.PDP)
        self._evidence(product, Surface.PDP)

        switch_store_settings(PRODUCT_SUGGESTIONS_ENABLED=False)
        assert learn_slot_weights() == {"slots": 0}
        pdp.refresh_from_db()
        assert pdp.weights == {}

        switch_store_settings(PRODUCT_SUGGESTIONS_ENABLED=True)
        assert learn_slot_weights() == {"slots": 1}
        pdp.refresh_from_db()
        assert pdp.weights == {C: 1.0, CAT: 0.5}


def test_the_nightly_pass_is_scheduled_for_every_store():
    from tenant import tasks as tenant_tasks

    entry = django_settings.CELERY_BEAT_SCHEDULE["learn-recommendation-weights"]
    assert entry["task"] == "tenant.tasks.fanout_learn_recommendation_weights"
    with patch("tenant.tasks.run_for_all_tenants") as run:
        tenant_tasks.fanout_learn_recommendation_weights()
    run.assert_called_once_with("recommendation.tasks.learn_slot_weights")
