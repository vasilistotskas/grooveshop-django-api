"""The feedback rows: one per item, enums re-validated after the
broker hop, and nothing written that the aggregation could not read."""

from __future__ import annotations

import uuid

import pytest

from product.factories.product import ProductFactory
from recommendation.enum import EventKind, StrategyCode, Surface
from recommendation.events import record_events
from recommendation.models import RecommendationEvent

pytestmark = pytest.mark.django_db


def _items(*products, strategy=StrategyCode.CURATED):
    return [
        {"product_id": p.id, "strategy": strategy, "position": i}
        for i, p in enumerate(products)
    ]


def test_one_row_per_item_sharing_the_impression():
    a, b = ProductFactory(num_images=0), ProductFactory(num_images=0)
    impression = uuid.uuid4()

    written = record_events(
        kind=EventKind.IMPRESSION,
        surface=Surface.PDP,
        impression_id=str(impression),
        items=_items(a, b),
        session_key="s" * 60,
    )

    assert written == 2
    rows = RecommendationEvent.objects.filter(impression_id=impression)
    assert {r.product_id for r in rows} == {a.id, b.id}
    assert {r.position for r in rows} == {0, 1}
    assert all(len(r.session_key) == 40 for r in rows)


def test_unknown_kind_or_surface_writes_nothing():
    a = ProductFactory(num_images=0)
    assert (
        record_events(
            kind="purchase",
            surface=Surface.PDP,
            impression_id=str(uuid.uuid4()),
            items=_items(a),
        )
        == 0
    )
    assert (
        record_events(
            kind=EventKind.CLICK,
            surface="sidebar",
            impression_id=str(uuid.uuid4()),
            items=_items(a),
        )
        == 0
    )
    assert not RecommendationEvent.objects.exists()


def test_items_with_an_unknown_strategy_are_dropped_individually():
    a, b = ProductFactory(num_images=0), ProductFactory(num_images=0)
    items = _items(a) + _items(b, strategy="telepathy")

    written = record_events(
        kind=EventKind.CLICK,
        surface=Surface.CART,
        impression_id=str(uuid.uuid4()),
        items=items,
    )

    assert written == 1
    assert list(
        RecommendationEvent.objects.values_list("product_id", flat=True)
    ) == [a.id]
