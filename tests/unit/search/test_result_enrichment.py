"""Query-count regression test for search-result enrichment (G0336/G0351).

Hydrating Meilisearch product hits must stay O(1) queries regardless of the
number of hits — the per-hit serializer reads master.{likes_count,
review_average, main_image_path, vat, …}, which N+1'd before
``ProductTranslation.get_search_result_queryset()`` prefetched them. This
exercises the DB hydration path directly, so it needs no live Meilisearch.
"""

from __future__ import annotations

import pytest

from product.factories.product import ProductFactory
from product.models.product import ProductTranslation
from search.serializers import ProductTranslationSerializer
from tests.utils import count_queries


def _serialize_hits(pks):
    qs = ProductTranslation.get_search_result_queryset().filter(pk__in=pks)
    return [ProductTranslationSerializer(obj, context={}).data for obj in qs]


def _en_translation_pks(products):
    return list(
        ProductTranslation.objects.filter(
            master__in=products, language_code="en"
        ).values_list("pk", flat=True)
    )


@pytest.mark.django_db
def test_search_result_enrichment_is_constant_query():
    products = ProductFactory.create_batch(2, num_images=1, num_reviews=2)
    with count_queries() as small:
        _serialize_hits(_en_translation_pks(products))

    products += ProductFactory.create_batch(3, num_images=1, num_reviews=2)
    with count_queries() as large:
        _serialize_hits(_en_translation_pks(products))

    assert small.count == large.count, (
        f"Search enrichment query count grew from {small.count} to "
        f"{large.count} when hits grew — N+1 regression."
    )
