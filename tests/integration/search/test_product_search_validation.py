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

from core.api.query_params import MAX_INT_CSV_ITEMS


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

    @pytest.mark.parametrize(
        ("param", "error_key"),
        [
            ("categories", "categories"),
            ("attribute_values", "attributeValues"),
            ("brands", "brands"),
        ],
    )
    def test_id_list_over_the_cap_returns_400(self, param, error_key):
        too_many = ",".join(str(i) for i in range(1, MAX_INT_CSV_ITEMS + 2))
        response = self.client.get(self.url, {param: too_many, "query": "x"})
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        # The error body is camelCased like every response.
        assert response.json()["fields"][error_key][0]["code"] == (
            "too_many_values"
        )

    def test_repeated_ids_count_once(self):
        # A mangled or stale link that repeats an id must not empty the
        # listing: the cap counts distinct ids.
        repeated = ",".join(["7"] * (MAX_INT_CSV_ITEMS + 1))
        response = self.client.get(self.url, {"brands": repeated, "query": "x"})
        assert response.status_code != status.HTTP_400_BAD_REQUEST
