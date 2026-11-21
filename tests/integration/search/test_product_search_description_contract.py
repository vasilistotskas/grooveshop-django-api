"""A product without a description, as a search hit.

``ProductTranslation.description`` still allows NULL while the search hit
declares a string, so the storefront would answer 422 to this hit. The
column becomes NOT NULL with ``""`` as the empty value (release 1 stops
writing NULL and backfills, release 2 adds the constraint); until then this
test meets the drift, and its marker comes off with the fix.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from product.factories.product import ProductFactory
from product.models.product import ProductTranslation
from tests.conftest import requires_meilisearch
from tests.utils.meilisearch import LiveSearchIndex, unique_marker


@requires_meilisearch
@pytest.mark.django_db
@pytest.mark.known_schema_drift(
    "GET",
    "/api/v1/search/product",
    200,
    at="$.results[*].description",
    issue=684,
)
def test_a_product_without_a_description_is_found():
    marker = unique_marker()

    with LiveSearchIndex() as index:
        product = ProductFactory(
            active=True,
            num_reviews=0,
            translations=[ProductTranslation(language_code="en", name=marker)],
        )
        index.add(product.translations.get())
        response = APIClient().get(
            reverse("search-product"), {"query": marker, "language_code": "en"}
        )

    assert response.status_code == status.HTTP_200_OK
    [hit] = response.json()["results"]
    assert hit["master"] == product.id
    assert hit["description"] is None
