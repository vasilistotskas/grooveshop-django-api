"""Query-count regression test for search-result enrichment (G0336/G0351).

Hydrating Meilisearch product hits must stay O(1) queries regardless of the
number of hits — the per-hit serializer reads master.{likes_count,
review_average, main_image_path, vat, …}, which N+1'd before
``ProductTranslation.get_search_result_queryset()`` prefetched them. This
exercises the DB hydration path directly, so it needs no live Meilisearch.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import PropertyMock, patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from djmoney.money import Money

from product.factories.image import ProductImageFactory
from product.factories.product import ProductFactory
from product.models.image import ProductImage
from product.models.product import Product, ProductTranslation
from search.serializers import ProductTranslationSerializer
from tenant.features import feature_on


def _serialize_hits(pks):
    qs = ProductTranslation.get_search_result_queryset().filter(pk__in=pks)
    # The search views decide once and hand the answer to every hit.
    context = {"reviews_visible": feature_on("product_reviews")}
    return [
        ProductTranslationSerializer(obj, context=context).data for obj in qs
    ]


def _en_translation_pks(products):
    return list(
        ProductTranslation.objects.filter(
            master__in=products, language_code="en"
        ).values_list("pk", flat=True)
    )


def _with_main_image(products):
    # The factory's ``is_main`` is a random boolean: every product here
    # gets a main image, so each hit reads its path and size.
    for product in products:
        ProductImageFactory(product=product, is_main=True)
    return products


@pytest.mark.django_db
def test_search_result_enrichment_is_constant_query():
    products = _with_main_image(
        ProductFactory.create_batch(2, num_images=0, num_reviews=2)
    )
    with CaptureQueriesContext(connection) as small:
        _serialize_hits(_en_translation_pks(products))

    products += _with_main_image(
        ProductFactory.create_batch(3, num_images=0, num_reviews=2)
    )
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
def test_a_hit_carries_the_stored_size_of_its_main_image():
    """The size is the main image's stored one, as every other product
    endpoint states it, even when another image comes first in the
    gallery; a product with no image has no size."""
    pictured = ProductFactory(num_images=0, num_reviews=0)
    ProductImageFactory(product=pictured, is_main=False, sort_order=0)
    ProductImageFactory(
        product=pictured,
        is_main=True,
        sort_order=1,
        image__width=800,
        image__height=600,
    )
    bare = ProductFactory(num_images=0, num_reviews=0)

    by_master = {
        row["master"]: row
        for row in _serialize_hits(_en_translation_pks([pictured, bare]))
    }

    assert by_master[pictured.id]["main_image_width"] == 800
    assert by_master[pictured.id]["main_image_height"] == 600
    assert by_master[bare.id]["main_image_width"] is None
    assert by_master[bare.id]["main_image_height"] is None


@pytest.mark.django_db
def test_a_main_image_of_unknown_size_has_a_path_and_no_size():
    product = ProductFactory(num_images=0, num_reviews=0)
    main = ProductImageFactory(product=product, is_main=True)
    ProductImage.objects.filter(pk=main.pk).update(
        image_width=None, image_height=None
    )

    (row,) = _serialize_hits(_en_translation_pks([product]))

    assert row["main_image_path"]
    assert row["main_image_width"] is None
    assert row["main_image_height"] is None


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


@pytest.mark.django_db
def test_the_final_price_is_computed_once_per_hit():
    products = ProductFactory.create_batch(3, num_images=0, num_reviews=0)

    with patch.object(
        Product,
        "final_price",
        new_callable=PropertyMock,
        return_value=Money(5, "EUR"),
    ) as final_price:
        rows = _serialize_hits(_en_translation_pks(products))

    assert {row["final_price"] for row in rows} == {5.0}
    assert final_price.call_count == len(products)


@pytest.mark.django_db
def test_hits_are_returned_in_ranking_order_each_with_its_own_data():
    products = ProductFactory.create_batch(4, num_images=0, num_reviews=0)
    pks = _en_translation_pks(products)
    ranked = [pks[2], pks[0], pks[3], pks[1]]
    hits = [
        {"id": pk, "_formatted": {"rank": position}}
        for position, pk in enumerate(ranked)
    ]

    with CaptureQueriesContext(connection) as ctx:
        results = ProductTranslation.meilisearch._enrich_results({"hits": hits})

    assert [row["object"].pk for row in results] == ranked
    assert [row["_formatted"]["rank"] for row in results] == [0, 1, 2, 3]
    # The order is applied in Python, not by a CASE over every pk.
    assert not any(
        '"product_product_translation"."id" =' in q["sql"]
        for q in ctx.captured_queries
    )


@pytest.mark.django_db
def test_a_hit_whose_row_is_gone_is_skipped():
    (product,) = ProductFactory.create_batch(1, num_images=0, num_reviews=0)
    (pk,) = _en_translation_pks([product])
    gone = (
        ProductTranslation.objects.order_by("-pk").values_list("pk", flat=True)[
            0
        ]
        + 1000
    )

    results = ProductTranslation.meilisearch._enrich_results(
        {"hits": [{"id": gone}, {"id": pk}]}
    )

    assert [row["object"].pk for row in results] == [pk]
