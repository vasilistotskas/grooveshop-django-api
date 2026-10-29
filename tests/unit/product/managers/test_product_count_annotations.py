"""The like and review annotations are correlated subqueries, not joins.

A ``Count``/``Avg`` over ``favourited_by`` and ``reviews`` joins both
tables and aggregates, which puts every other annotation into the GROUP
BY. ``with_offer_kind`` is one: its promotion subplan then ran once per
joined favourite x review row instead of once per product, and a listing
of 12 products took 1.9 s against 0.15 s without the offer.
"""

from __future__ import annotations

import pytest

from product.enum.review import ReviewStatus
from product.factories.favourite import ProductFavouriteFactory
from product.factories.product import ProductFactory
from product.factories.review import ProductReviewFactory
from product.models.product import Product, ProductTranslation
from user.factories.account import UserAccountFactory


@pytest.mark.django_db
def test_listing_queryset_has_no_group_by():
    sql = str(Product.objects.for_list().with_offer_kind().query)

    assert "GROUP BY" not in sql


@pytest.mark.django_db
def test_meilisearch_indexing_queryset_has_no_group_by():
    sql = str(ProductTranslation.get_meilisearch_queryset().query)

    assert "GROUP BY" not in sql


@pytest.mark.django_db
def test_counts_match_the_rows_they_describe():
    product = ProductFactory(num_images=0, num_reviews=0)
    bare = ProductFactory(num_images=0, num_reviews=0)
    for user in UserAccountFactory.create_batch(2):
        ProductFavouriteFactory(product=product, user=user)
    for rate, status in (
        (4, ReviewStatus.TRUE),
        (2, ReviewStatus.TRUE),
        (5, ReviewStatus.NEW),
        (1, ReviewStatus.FALSE),
    ):
        ProductReviewFactory(product=product, rate=rate, status=status)

    rows = {
        row.pk: row
        for row in Product.objects.filter(pk__in=[product.pk, bare.pk])
        .with_counts()
        .order_by()
    }

    assert rows[product.pk].likes_count == 2
    assert rows[product.pk].reviews_count == 2
    assert rows[product.pk].review_average == 3.0
    assert rows[bare.pk].likes_count == 0
    assert rows[bare.pk].reviews_count == 0
    assert rows[bare.pk].review_average == 0.0
