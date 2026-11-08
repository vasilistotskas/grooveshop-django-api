"""A requested ordering that ties must still page without repeats or gaps.

Rows equal on every requested key came back in plan order, which can
differ between two pages of one listing. ``CamelCaseOrderingFilter``
ends every ordering that is not already total with the primary key.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.request import Request
from rest_framework.test import APIClient, APIRequestFactory

from core.filters.camel_case_ordering import CamelCaseOrderingFilter
from product.factories.product import ProductFactory
from product.models.product import Product

pytestmark = pytest.mark.django_db


def test_tied_values_across_a_page_boundary_never_repeat_or_skip():
    ProductFactory.create_batch(
        30, active=True, stock=1, num_images=0, num_reviews=0, view_count=7
    )
    client = APIClient()
    url = reverse("product-list")

    seen: list[int] = []
    for page in (1, 2, 3):
        response = client.get(
            url, {"ordering": "-viewCount", "page": page, "pageSize": 12}
        )
        assert response.status_code == 200
        seen += [row["id"] for row in response.json()["results"]]

    assert len(seen) == 30
    assert len(set(seen)) == 30


def test_the_order_ends_with_the_primary_key():
    ProductFactory(active=True, num_images=0, num_reviews=0)
    client = APIClient()
    with CaptureQueriesContext(connection) as captured:
        client.get(reverse("product-list"), {"ordering": "-viewCount"})

    sql = next(
        q["sql"]
        for q in captured
        if "ORDER BY" in q["sql"] and "LIMIT" in q["sql"]
    )
    assert (
        sql.rstrip()
        .split("ORDER BY")[1]
        .split("LIMIT")[0]
        .strip()
        .endswith('"product_product"."id" ASC')
    )


@pytest.mark.parametrize(
    ("ordering", "expected"),
    [
        # A unique, non-null key is already total: no tiebreak is added.
        (["sku"], ("sku",)),
        (["-sku"], ("-sku",)),
        (["view_count"], ("view_count", "pk")),
        (["-view_count", "id"], ("-view_count", "id")),
    ],
)
def test_the_primary_key_is_added_only_when_the_order_is_not_total(
    monkeypatch, ordering, expected
):
    monkeypatch.setattr(
        CamelCaseOrderingFilter,
        "get_ordering",
        lambda self, request, queryset, view: ordering,
    )
    request = Request(APIRequestFactory().get("/"))

    queryset = CamelCaseOrderingFilter().filter_queryset(
        request, Product.objects.all(), view=None
    )

    assert queryset.query.order_by == expected


def test_a_translated_key_still_gets_the_primary_key(monkeypatch):
    """A ``translations__`` key orders by an expression (the value in the
    request's language), which ``totally_ordered`` cannot count as
    unique, so the primary key still ends the order."""
    monkeypatch.setattr(
        CamelCaseOrderingFilter,
        "get_ordering",
        lambda self, request, queryset, view: ["translations__name"],
    )
    request = Request(APIRequestFactory().get("/"))

    queryset = CamelCaseOrderingFilter().filter_queryset(
        request, Product.objects.all(), view=None
    )

    *keys, last = queryset.query.order_by
    assert len(keys) == 1
    assert not isinstance(keys[0], str)
    assert last == "pk"
