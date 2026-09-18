"""``?ordering=`` on a product's review listing.

The action built its queryset by hand and never ran ``filter_queryset``,
so the sort was silently ignored; the storefront's proxy dropped the
query on top. Both now flow.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from product.enum.review import ReviewStatus
from product.factories.product import ProductFactory
from product.factories.review import ProductReviewFactory
from product.models.review import ProductReview
from user.factories import UserAccountFactory


def _ids(response) -> list[int]:
    assert response.status_code == 200, response.content
    return [row["id"] for row in response.json()["results"]]


@pytest.mark.django_db
def test_reviews_sort_both_ways_and_by_rate():
    product = ProductFactory()
    older = ProductReviewFactory(
        product=product,
        user=UserAccountFactory(),
        rate=2,
        status=ReviewStatus.TRUE,
        is_published=True,
    )
    newer = ProductReviewFactory(
        product=product,
        user=UserAccountFactory(),
        rate=5,
        status=ReviewStatus.TRUE,
        is_published=True,
    )
    ProductReview.objects.filter(pk=older.pk).update(
        created_at=timezone.now() - timedelta(days=2)
    )
    url = reverse("product-reviews", args=[product.pk])
    client = APIClient()

    assert _ids(client.get(url, {"ordering": "createdAt"})) == [
        older.pk,
        newer.pk,
    ]
    assert _ids(client.get(url, {"ordering": "-createdAt"})) == [
        newer.pk,
        older.pk,
    ]
    assert _ids(client.get(url, {"ordering": "rate"})) == [older.pk, newer.pk]
    # Default: newest first.
    assert _ids(client.get(url)) == [newer.pk, older.pk]
    # A PRODUCT column is not this listing's; it is dropped, not 400.
    assert _ids(client.get(url, {"ordering": "availabilityPriority"})) == [
        newer.pk,
        older.pk,
    ]
