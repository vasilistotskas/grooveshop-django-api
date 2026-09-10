"""The suggestions endpoint runs on every product page: its cost must
not grow with how many candidates exist, nor with how many seeds the
cart passes. Growth comparison, not absolute counts — the house idiom.

Each measurement is preceded by an unmeasured request: the first
request after a write pays for the per-tenant context, and the eager
Celery task that records the impression leaves django-tenants with a
pending ``SET search_path`` that the next cursor issues. Starting both
measurements from the same state keeps the comparison about the
engine.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from product.factories.category import ProductCategoryFactory
from product.factories.product import ProductFactory
from recommendation.enum import Surface
from tests.utils import count_queries

pytestmark = pytest.mark.django_db

URL = reverse("recommendation-list")


def _product(**kwargs):
    kwargs.setdefault("active", True)
    kwargs.setdefault("stock", 5)
    kwargs.setdefault("num_images", 0)
    kwargs.setdefault("num_reviews", 0)
    return ProductFactory(**kwargs)


def _measure(client, query):
    client.get(URL, query)
    with count_queries() as counted:
        response = client.get(URL, query)
    return counted.count, response


def test_cost_does_not_grow_with_the_number_of_candidates():
    category = ProductCategoryFactory(active=True)
    seed = _product(category=category)
    client = APIClient()
    query = {"surface": Surface.PDP, "seed": seed.id, "limit": 12}

    _product(category=category)
    _product(category=category)
    few, response = _measure(client, query)
    assert len(response.data["items"]) == 2

    for _ in range(4):
        _product(category=category)
    many, response = _measure(client, query)
    assert len(response.data["items"]) == 6

    assert many == few, (
        f"suggestions cost grew from {few} to {many} queries with four "
        f"more candidates — hydration is missing for_list(), or a "
        f"strategy is querying per candidate"
    )


def test_cost_does_not_grow_with_the_number_of_seeds():
    category = ProductCategoryFactory(active=True)
    seeds = [_product(category=category) for _ in range(4)]
    for _ in range(3):
        _product(category=category)
    client = APIClient()

    def _query(count):
        return {
            "surface": Surface.CART,
            "seeds": ",".join(str(s.id) for s in seeds[:count]),
        }

    one, _ = _measure(client, _query(1))
    four, _ = _measure(client, _query(4))

    assert four == one, (
        f"suggestions cost grew from {one} to {four} queries with three "
        f"more seeds — a strategy is answering per seed instead of "
        f"through candidates_for()"
    )
