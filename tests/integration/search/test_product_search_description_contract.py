"""A product without a description, as a search hit.

The search hit declares ``description`` a string, and a translation saved
with none used to store NULL, served as ``null``, which the storefront
answers 422 to (#684). Its empty value is now ``""``, written by every
writer the code has; the column's NOT NULL constraint follows in the next
release.
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
    assert hit["description"] == ""
