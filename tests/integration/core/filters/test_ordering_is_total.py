"""A requested ordering that ties must still page without repeats or gaps.

Rows equal on every requested key came back in plan order, which can
differ between two pages of one listing. ``CamelCaseOrderingFilter``
now ends every ordering with the primary key.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APIClient

from product.factories.product import ProductFactory

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
