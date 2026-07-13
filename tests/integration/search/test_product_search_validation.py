"""Parameter-validation tests for the product search endpoint.

These do not require a live Meilisearch: invalid query parameters are
rejected with a 400 during parsing, before any Meilisearch call is made.
Regression for the public endpoint returning 500 on unparseable numeric
filters.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient


@pytest.mark.django_db
class TestProductSearchParamValidation:
    def setup_method(self):
        self.client = APIClient()
        self.url = reverse("search-product")

    @pytest.mark.parametrize(
        "params",
        [
            {"price_min": "abc"},
            {"price_max": "1o0"},
            {"likes_min": "many"},
            {"views_min": "twelve"},
            {"categories": "1,two,3"},
            {"attribute_values": "x"},
        ],
    )
    def test_invalid_numeric_params_return_400_not_500(self, params):
        response = self.client.get(self.url, {**params, "query": "shoe"})
        assert response.status_code == status.HTTP_400_BAD_REQUEST
