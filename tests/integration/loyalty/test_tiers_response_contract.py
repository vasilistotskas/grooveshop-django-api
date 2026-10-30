"""``GET /loyalty/tiers`` answers a bare array, and the schema says so.

The action serialises every tier with ``many=True`` and never paginates:
the ladder is a handful of rows the storefront shows whole. The schema
documented a ``PaginatedLoyaltyTierList`` envelope with ``page`` and
``pageSize`` parameters, so the storefront's generated response schema
could not parse the real body and its proxy validated against a
hand-written ``z.array(zLoyaltyTier)`` instead.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from loyalty.models.tier import LoyaltyTier
from tests.utils.openapi import assert_matches_response, response_schema
from tests.utils.store_settings import switch_store_settings

TIERS_PATH = "/api/v1/loyalty/tiers"


def test_the_schema_documents_a_bare_array(openapi_schema):
    schema = response_schema(openapi_schema, TIERS_PATH)
    parameters = {
        parameter["name"]
        for parameter in openapi_schema["paths"][TIERS_PATH]["get"][
            "parameters"
        ]
    }

    assert schema == {
        "type": "array",
        "items": {"$ref": "#/components/schemas/LoyaltyTier"},
    }
    assert parameters == {"languageCode"}


@pytest.mark.django_db
def test_every_tier_comes_back_in_one_array_ordered_by_level(openapi_schema):
    switch_store_settings(LOYALTY_ENABLED=True)
    for level, multiplier in ((10, "1.50"), (1, "1.10")):
        tier = LoyaltyTier(
            required_level=level, points_multiplier=Decimal(multiplier)
        )
        tier.set_current_language("el")
        tier.name = f"Tier {level}"
        tier.save()

    response = APIClient().get(reverse("loyalty:loyalty-tiers"))

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert isinstance(body, list)
    assert [row["requiredLevel"] for row in body] == [1, 10]
    assert_matches_response(openapi_schema, TIERS_PATH, body)
