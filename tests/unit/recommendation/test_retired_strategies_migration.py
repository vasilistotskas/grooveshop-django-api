"""Migration 0006 takes ``semantic`` and ``co_view`` out of every slot.

Neither was ever implemented — the engine skipped them on every request
— and the admin no longer offers them, so a chain still naming one
would fail the slot's own validation the next time a merchant saved it.
"""

from __future__ import annotations

import importlib
import uuid

import pytest
from django.apps import apps

from product.factories.product import ProductFactory
from recommendation.enum import EventKind, Surface
from recommendation.models import RecommendationEvent, RecommendationSlot

pytestmark = pytest.mark.django_db

_migration = importlib.import_module(
    "recommendation.migrations.0006_slot_learn_weights_and_built_strategies"
)


def _slot(surface, chain, weights=None):
    return RecommendationSlot.objects.create(
        surface=surface,
        strategy_chain=chain,
        weights=weights or {},
        limit=4,
        min_fill=1,
    )


def test_retired_codes_leave_the_chain_and_the_weights():
    slot = _slot(
        Surface.PDP,
        ["curated", "semantic", "category", "co_view"],
        {"semantic": 0.5, "category": 0.8},
    )

    _migration.strip_retired_strategies(apps, None)

    slot.refresh_from_db()
    assert slot.strategy_chain == ["curated", "category"]
    assert slot.weights == {"category": 0.8}
    slot.full_clean()


def test_a_chain_of_nothing_else_gets_a_strategy_that_answers_there():
    pdp = _slot(Surface.PDP, ["semantic"])
    empty_cart = _slot(Surface.EMPTY_CART, ["co_view"])

    _migration.strip_retired_strategies(apps, None)

    pdp.refresh_from_db()
    empty_cart.refresh_from_db()
    assert pdp.strategy_chain == ["curated"]
    assert empty_cart.strategy_chain == ["popular"]
    pdp.full_clean()
    empty_cart.full_clean()


def test_untouched_slots_are_not_rewritten():
    slot = _slot(Surface.CART, ["curated", "popular"])
    before = slot.updated_at

    _migration.strip_retired_strategies(apps, None)

    slot.refresh_from_db()
    assert slot.strategy_chain == ["curated", "popular"]
    assert slot.updated_at == before


def test_events_under_a_retired_code_are_removed():
    product = ProductFactory(num_images=0, num_reviews=0)
    for strategy in ("semantic", "curated"):
        RecommendationEvent.objects.create(
            surface=Surface.PDP,
            strategy=strategy,
            kind=EventKind.CLICK,
            product=product,
            impression_id=uuid.uuid4(),
        )

    _migration.strip_retired_strategies(apps, None)

    assert list(
        RecommendationEvent.objects.values_list("strategy", flat=True)
    ) == ["curated"]
