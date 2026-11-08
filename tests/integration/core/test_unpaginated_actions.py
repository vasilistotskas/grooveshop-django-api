"""``?pagination=false`` returns a whole list only where a view opts in.

It used to work on every ``BaseModelViewSet`` list, so any caller, an
anonymous one on the public product list included, could take a whole
table in one response past the paginator's maximum page size. A view now
lists the actions that honour it in ``unpaginated_actions``, and every
other list ignores it.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from country.factories import CountryFactory
from product.factories.product import ProductFactory

pytestmark = pytest.mark.django_db

PAGE_SIZE = 2


def test_an_opted_in_list_returns_every_row():
    CountryFactory.create_batch(PAGE_SIZE + 3)

    response = APIClient().get(
        reverse("country-list"),
        {"pagination": "false", "pageSize": PAGE_SIZE},
    )

    assert response.status_code == 200
    assert isinstance(response.json(), list)
    assert len(response.json()) >= PAGE_SIZE + 3


def test_any_other_list_ignores_it_and_stays_paginated():
    ProductFactory.create_batch(
        PAGE_SIZE + 3, active=True, num_images=0, num_reviews=0
    )

    response = APIClient().get(
        reverse("product-list"),
        {"pagination": "false", "pageSize": PAGE_SIZE},
    )

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, dict)
    assert len(body["results"]) == PAGE_SIZE
    assert body["count"] >= PAGE_SIZE + 3
