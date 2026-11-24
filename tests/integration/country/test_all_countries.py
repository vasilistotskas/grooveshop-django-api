"""``GET /api/v1/country/all``: every country in one array.

The storefront's country pickers need the whole list (about 250 rows,
more than the paginator's largest page). It used to read it with
``?pagination=false`` on the list, which answered a bare array where the
operation documents the paginated envelope (#684). This route documents
the array, and filters and orders as the list does.
"""

from __future__ import annotations

from contextlib import ExitStack

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from country.factories import CountryFactory
from country.models import Country
from shipping.factories import ShippingProviderFactory, ShippingRateFactory
from tests.utils.shipping import registered_carrier

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _registered_carriers():
    """The carriers these tests make up need an adapter: checkout's gate
    offers none without one, and neither does a shippable country."""
    with ExitStack() as stack:
        for code in ("all_countries_rate",):
            stack.enter_context(registered_carrier(code))
        yield


def test_it_answers_every_country_past_the_list_page():
    # More rows than the list's default page of 12.
    CountryFactory.create_batch(15)

    response = APIClient().get(reverse("country-all"))

    assert response.status_code == status.HTTP_200_OK
    codes = {row["alpha2"] for row in response.json()}
    assert codes == set(Country.objects.values_list("alpha_2", flat=True))


def test_it_filters_as_the_list_does(monkeypatch):
    from django.db import DEFAULT_DB_ALIAS, connections

    # On a store's own host, where ``shippable`` reads its rates.
    monkeypatch.setattr(
        connections[DEFAULT_DB_ALIAS], "schema_name", "webside", raising=False
    )
    rated, unrated = CountryFactory(), CountryFactory()
    ShippingRateFactory(
        provider=ShippingProviderFactory(code="all_countries_rate"),
        country=rated,
    )

    response = APIClient().get(reverse("country-all"), {"shippable": "true"})

    codes = {row["alpha2"] for row in response.json()}
    assert rated.alpha_2 in codes
    assert unrated.alpha_2 not in codes


def _countries_created_in_ascending_code_order(count: int) -> list[str]:
    """Countries whose ``sort_order`` (creation order) runs opposite to a
    descending code order, so a sort that is not applied shows."""
    codes = sorted(f"Q{chr(ord('A') + n)}" for n in range(count))
    for code in codes:
        CountryFactory(alpha_2=code, alpha_3=f"{code}X")
    return codes


def test_it_orders_by_the_requested_key():
    codes = _countries_created_in_ascending_code_order(3)

    response = APIClient().get(reverse("country-all"), {"ordering": "-alpha2"})

    listed = [row["alpha2"] for row in response.json()]
    assert [code for code in listed if code in codes] == codes[::-1]


def test_without_a_key_it_orders_as_the_list_does():
    _countries_created_in_ascending_code_order(3)
    client = APIClient()

    whole = [row["alpha2"] for row in client.get(reverse("country-all")).json()]
    first_page = [
        row["alpha2"]
        for row in client.get(reverse("country-list")).json()["results"]
    ]

    assert whole[: len(first_page)] == first_page
