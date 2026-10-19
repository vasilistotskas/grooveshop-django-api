"""The admin's "Recommendation candidates" list is the output of this
pipeline: a product save rebuilds that product's rows, and a nightly
pass, fanned out per store, rebuilds the whole catalogue's. Only the
precomputed strategies (``attributes``, ``co_purchase``) write rows; the
live ones answer per request and never appear there.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.conf import settings

from product.factories.attribute import AttributeFactory
from product.factories.attribute_value import AttributeValueFactory
from product.factories.product import ProductFactory
from product.models import ProductAttribute, ProductRelation
from recommendation.context import invalidate_tenant_context
from recommendation.enum import StrategyCode
from recommendation.models import RecommendationCandidate
from recommendation.tasks import (
    recompute_all_candidates,
    recompute_candidates_for_product,
)
from tests.utils.store_settings import switch_store_settings

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _store_shows_suggestions():
    switch_store_settings(PRODUCT_SUGGESTIONS_ENABLED=True)


def _product(**kwargs):
    kwargs.setdefault("active", True)
    kwargs.setdefault("stock", 5)
    kwargs.setdefault("num_images", 0)
    kwargs.setdefault("num_reviews", 0)
    return ProductFactory(**kwargs)


def _sharing_a_value(count: int):
    value = AttributeValueFactory(
        attribute=AttributeFactory(active=True), active=True
    )
    products = [_product() for _ in range(count)]
    for product in products:
        ProductAttribute.objects.create(product=product, attribute_value=value)
    # The facts strategies consult are cached; these products were
    # created before their attributes, as an import would.
    invalidate_tenant_context()
    return products


def _rows(product) -> set[tuple[int, str]]:
    return set(
        RecommendationCandidate.objects.filter(product=product).values_list(
            "candidate_id", "strategy"
        )
    )


def test_a_product_rebuild_writes_its_precomputed_candidates():
    seed, partner = _sharing_a_value(2)
    ProductRelation.objects.create(from_product=seed, to_product=partner)

    recompute_candidates_for_product(seed.id)

    # ``curated`` names the same partner but answers live: no row.
    assert _rows(seed) == {(partner.id, StrategyCode.ATTRIBUTES)}


def test_a_product_save_rebuilds_its_rows():
    seed, partner = _sharing_a_value(2)

    seed.save()

    assert _rows(seed) == {(partner.id, StrategyCode.ATTRIBUTES)}


def test_the_nightly_pass_fills_the_whole_catalogue():
    first, second, third = _sharing_a_value(3)

    result = recompute_all_candidates()

    assert result["failed"] == 0
    assert _rows(first) == {
        (second.id, StrategyCode.ATTRIBUTES),
        (third.id, StrategyCode.ATTRIBUTES),
    }
    assert RecommendationCandidate.objects.count() == 6


def test_a_rebuild_drops_rows_its_strategy_no_longer_supports():
    seed, _ = _sharing_a_value(2)
    recompute_all_candidates()
    assert _rows(seed)

    ProductAttribute.objects.all().delete()
    invalidate_tenant_context()
    recompute_all_candidates()

    assert not RecommendationCandidate.objects.exists()


def test_nothing_is_computed_while_the_store_shows_no_suggestions():
    switch_store_settings(PRODUCT_SUGGESTIONS_ENABLED=False)
    seed, _ = _sharing_a_value(2)

    recompute_candidates_for_product(seed.id)
    recompute_all_candidates()

    assert not RecommendationCandidate.objects.exists()


def test_the_nightly_pass_is_scheduled_for_every_store():
    from tenant import tasks as tenant_tasks

    entry = settings.CELERY_BEAT_SCHEDULE["recompute-recommendation-candidates"]
    assert (
        entry["task"]
        == "tenant.tasks.fanout_recompute_recommendation_candidates"
    )
    with patch("tenant.tasks.run_for_all_tenants") as run:
        tenant_tasks.fanout_recompute_recommendation_candidates()
    run.assert_called_once_with("recommendation.tasks.recompute_all_candidates")
