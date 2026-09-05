"""The metadata filters must actually narrow, and the staff one must gate.

`?metadataHasKey=promo` used to answer 200 with the entire unfiltered
list — the mixin declaring it was a plain class, so django-filter never
collected the filter and the unknown parameter was ignored.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from product.factories.product import ProductFactory
from tests.utils.staff import store_staff
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db


def _ids(response):
    data = response.data
    rows = (
        data["results"]
        if isinstance(data, dict) and "results" in data
        else data
    )
    return {row["id"] for row in rows}


@pytest.fixture
def tagged_and_untagged():
    tagged = ProductFactory(active=True, metadata={"promo": "spring"})
    untagged = ProductFactory(active=True, metadata={})
    return tagged, untagged


def test_metadata_has_key_narrows_the_list(tagged_and_untagged):
    tagged, untagged = tagged_and_untagged

    response = APIClient().get(
        reverse("product-list"), {"metadataHasKey": "promo"}
    )

    returned = _ids(response)
    assert tagged.pk in returned
    assert untagged.pk not in returned, (
        "the filter was ignored and the whole list came back"
    )


def test_metadata_contains_rejects_unparseable_json():
    response = APIClient().get(
        reverse("product-list"), {"metadataContains": "not json"}
    )

    assert response.status_code == 200
    assert _ids(response) == set()


def test_private_metadata_is_not_queryable_by_the_public():
    """The staff gate on this filter was dead code until now."""
    private = ProductFactory(active=True, private_metadata={"cost": "1.00"})
    public = ProductFactory(active=True, private_metadata={})

    client = APIClient()
    client.force_authenticate(user=UserAccountFactory())
    response = client.get(
        reverse("product-list"), {"privateMetadataHasKey": "cost"}
    )

    returned = _ids(response)
    assert {private.pk, public.pk} <= returned, (
        "a non-staff caller must not be able to probe private_metadata, "
        "so the filter is a no-op for them rather than a narrowing"
    )
