"""Query-count regression test for search-result enrichment (G0336/G0351).

Hydrating Meilisearch product hits must stay O(1) queries regardless of the
number of hits — the per-hit serializer reads master.{likes_count,
review_average, main_image_path, vat, …}, which N+1'd before
``ProductTranslation.get_search_result_queryset()`` prefetched them. This
exercises the DB hydration path directly, so it needs no live Meilisearch.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from product.factories.product import ProductFactory
from product.models.product import ProductTranslation
from search.serializers import ProductTranslationSerializer


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
    with CaptureQueriesContext(connection) as small:
        _serialize_hits(_en_translation_pks(products))

    products += ProductFactory.create_batch(3, num_images=1, num_reviews=2)
    with CaptureQueriesContext(connection) as large:
        _serialize_hits(_en_translation_pks(products))

    assert len(small) == len(large), (
        f"Search enrichment query count grew from {len(small)} to "
        f"{len(large)} when hits grew — N+1 regression."
    )


@pytest.mark.django_db
def test_a_zero_price_is_serialized_as_zero_not_null():
    """A free product costs 0.0, as every other product endpoint says;
    null would mean "no price". ``Money(0)`` is falsy, which is the trap.
    A priced product carries its amounts as floats."""
    free = ProductFactory(
        price=Decimal("0.00"),
        discount_percent=Decimal("0.00"),
        num_images=0,
        num_reviews=0,
    )
    priced = ProductFactory(
        price=Decimal("20.00"),
        discount_percent=Decimal("0.00"),
        num_images=0,
        num_reviews=0,
    )

    by_master = {
        row["master"]: row
        for row in _serialize_hits(_en_translation_pks([free, priced]))
    }

    assert by_master[free.id]["price"] == 0.0
    assert by_master[free.id]["final_price"] == 0.0
    assert by_master[priced.id]["price"] == 20.0
    assert by_master[priced.id]["final_price"] == float(
        priced.final_price.amount
    )


@pytest.mark.django_db
def test_hit_carries_its_category_name_in_the_hits_language():
    from product.factories.category import ProductCategoryFactory

    category = ProductCategoryFactory()
    for code, name in (("el", "Φορτιστές"), ("en", "Chargers")):
        category.set_current_language(code)
        category.name = name
    category.save()
    product = ProductFactory(category=category, num_images=0, num_reviews=0)

    translations = ProductTranslation.objects.filter(master=product)
    by_language = {
        row["language_code"]: row["category_name"]
        for row in _serialize_hits(
            list(translations.values_list("pk", flat=True))
        )
    }

    assert by_language["en"] == "Chargers"
    assert by_language["el"] == "Φορτιστές"


@pytest.mark.django_db
def test_category_names_are_loaded_in_one_query_for_all_hits():
    products = ProductFactory.create_batch(5, num_images=0, num_reviews=0)

    with CaptureQueriesContext(connection) as ctx:
        _serialize_hits(_en_translation_pks(products))

    category_translation_reads = [
        q["sql"]
        for q in ctx.captured_queries
        if '"product_productcategory_translation"' in q["sql"]
    ]
    assert len(category_translation_reads) == 1


@pytest.mark.django_db
def test_indexed_document_stores_the_category_name_per_language():
    """The index document of each translation row carries the category
    name in that row's language, not in whatever language the indexing
    worker had active."""
    from product.factories.category import ProductCategoryFactory

    category = ProductCategoryFactory()
    for code, name in (("el", "Φορτιστές"), ("en", "Chargers")):
        category.set_current_language(code)
        category.name = name
    category.save()
    product = ProductFactory(category=category, num_images=0, num_reviews=0)
    getter = ProductTranslation.get_additional_meili_fields()["category_name"]

    names = {
        row.language_code: getter(row)
        for row in ProductTranslation.get_search_result_queryset().filter(
            master=product
        )
    }

    assert names["en"] == "Chargers"
    assert names["el"] == "Φορτιστές"
