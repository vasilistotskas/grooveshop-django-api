"""``GET /api/v1/country/all``: every country in one array.

The storefront's country pickers need the whole list (about 250 rows,
more than the paginator's largest page). It used to read it with
``?pagination=false`` on the list, which answered a bare array where the
operation documents the paginated envelope (#684). This route documents
the array, and filters and orders as the list does.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from country.factories import CountryFactory
from country.models import Country
from shipping.factories import ShippingProviderFactory, ShippingRateFactory

pytestmark = pytest.mark.django_db

PAGE_SIZE = 2


def test_it_answers_every_country_past_any_page_size():
    CountryFactory.create_batch(PAGE_SIZE + 3)

    response = APIClient().get(reverse("country-all"), {"pageSize": PAGE_SIZE})

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


def test_it_orders_as_the_list_does():
    CountryFactory.create_batch(3)

    whole = APIClient().get(reverse("country-all"), {"ordering": "-alpha_2"})

    codes = [row["alpha2"] for row in whole.json()]
    assert codes == sorted(codes, reverse=True)
