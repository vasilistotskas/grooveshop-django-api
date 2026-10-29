"""``GET product/<pk>/reviews`` loads the product only to scope it.

The guard used ``for_detail()``, which prefetches the product's
translations, images, attributes and tags, all of which the review
serializer then fetched again for its own product brief.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from product.factories.product import ProductFactory
from product.factories.review import ProductReviewFactory

pytestmark = pytest.mark.django_db


def test_the_product_guard_loads_no_detail_relations():
    product = ProductFactory(num_images=1, num_reviews=0, active=True)
    ProductReviewFactory(product=product, status="TRUE")
    url = reverse("product-reviews", args=[product.pk])
    client = APIClient()
    client.get(url)

    with CaptureQueriesContext(connection) as captured:
        response = client.get(url)

    assert response.status_code == status.HTTP_200_OK
    sql = " ".join(query["sql"] for query in captured)
    assert "product_productattribute" not in sql
    assert "tag_taggeditem" not in sql
