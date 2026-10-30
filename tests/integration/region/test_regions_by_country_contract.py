"""``get_regions_by_country_alpha_2`` answers a bare array, as documented.

The action lists every region of one country and never paginates; the
schema documented a ``PaginatedRegionList`` envelope with page and
filter parameters the action ignores.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from country.factories import CountryFactory
from region.factories import RegionFactory
from tests.utils.openapi import assert_matches_response, response_schema

PATH = "/api/v1/region/{alpha}/get_regions_by_country_alpha_2"


def test_the_schema_documents_a_bare_array(openapi_schema):
    assert response_schema(openapi_schema, PATH) == {
        "type": "array",
        "items": {"$ref": "#/components/schemas/Region"},
    }


@pytest.mark.django_db
def test_the_response_is_every_region_of_the_country(openapi_schema):
    country = CountryFactory()
    regions = RegionFactory.create_batch(2, country=country)
    RegionFactory()

    response = APIClient().get(
        reverse("region-get-regions-by-country-alpha-2", args=[country.alpha_2])
    )

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert sorted(row["alpha"] for row in body) == sorted(
        region.alpha for region in regions
    )
    assert_matches_response(openapi_schema, PATH, body)
