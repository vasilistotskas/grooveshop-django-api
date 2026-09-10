"""Product and relation writes schedule a candidate rebuild for the
affected seed — through ``dispatch_on_commit`` so the schema travels
with the message — and visit-counter writes do not."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from product.factories.product import ProductFactory
from product.models import ProductRelation

pytestmark = pytest.mark.django_db


def _scheduled(mock) -> list[int]:
    return [call.kwargs["kwargs"]["product_id"] for call in mock.call_args_list]


def test_product_save_schedules_a_rebuild():
    with patch("recommendation.signals.dispatch_on_commit") as dispatch:
        product = ProductFactory(num_images=0, num_reviews=0)

    assert product.pk in _scheduled(dispatch)
    task = dispatch.call_args.args[0]
    assert task.name == "recommendation.tasks.recompute_candidates_for_product"


def test_visit_counters_do_not():
    product = ProductFactory(num_images=0, num_reviews=0)

    with patch("recommendation.signals.dispatch_on_commit") as dispatch:
        product.view_count += 1
        product.save(update_fields=["view_count"])
        product.click_score += 1
        product.save(update_fields=["click_score", "updated_at"])

    dispatch.assert_not_called()


def test_relation_save_and_delete_schedule_the_source_product():
    source = ProductFactory(num_images=0, num_reviews=0)
    target = ProductFactory(num_images=0, num_reviews=0)

    with patch("recommendation.signals.dispatch_on_commit") as dispatch:
        relation = ProductRelation.objects.create(
            from_product=source, to_product=target
        )
        relation.delete()

    assert _scheduled(dispatch) == [source.pk, source.pk]
