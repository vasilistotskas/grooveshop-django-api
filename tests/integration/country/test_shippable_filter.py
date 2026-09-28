"""``GET /api/v1/country?shippable=true`` — countries the current store
has an active ``ShippingRate`` for.

Plain pytest-style (not ``APITestCase``/``TestCase``): the autouse
``_reseed_countries``/``_reseed_shipping_providers`` fixtures
(``tests/conftest.py``) only fire for ``@pytest.mark.django_db``
function tests, so this file builds its own rate data explicitly
rather than leaning on those or the migration-seeded GR row.

The main test suite strips multi-tenancy (no ``TenantMainMiddleware``,
see ``tests/conftest.py``'s own docstring), so ``connection.schema_
name`` never leaves ``"public"`` here — every request would otherwise
look like it's on the platform host. ``as_tenant_host`` fakes a
non-public schema for the tests that exercise ordinary per-store
filtering; ``test_shippable_true_on_the_platform_host_returns_none``
is the one test that wants the real default.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from country.factories import CountryFactory
from shipping.enum import ShippingKind
from shipping.factories import ShippingProviderFactory, ShippingRateFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def as_tenant_host(monkeypatch):
    """Fake being on a store's own host, not the platform host."""
    from django.db import connection

    monkeypatch.setattr(connection, "schema_name", "webside", raising=False)


def test_shippable_true_returns_only_countries_with_an_active_rate(
    as_tenant_host,
):
    provider = ShippingProviderFactory(code="shippable_test")
    rated = CountryFactory()
    unrated = CountryFactory()
    ShippingRateFactory(provider=provider, country=rated)

    client = APIClient()
    response = client.get(reverse("country-list"), {"shippable": "true"})

    assert response.status_code == status.HTTP_200_OK
    codes = {row["alpha2"] for row in response.json()["results"]}
    assert rated.alpha_2 in codes
    assert unrated.alpha_2 not in codes


def test_shippable_true_excludes_a_country_whose_rate_is_inactive(
    as_tenant_host,
):
    provider = ShippingProviderFactory(code="shippable_rate_off")
    country = CountryFactory()
    ShippingRateFactory(provider=provider, country=country, is_active=False)

    client = APIClient()
    response = client.get(reverse("country-list"), {"shippable": "true"})

    codes = {row["alpha2"] for row in response.json()["results"]}
    assert country.alpha_2 not in codes


def test_shippable_true_excludes_a_country_whose_provider_is_inactive(
    as_tenant_host,
):
    provider = ShippingProviderFactory(
        code="shippable_prov_off", is_active=False
    )
    country = CountryFactory()
    ShippingRateFactory(provider=provider, country=country)

    client = APIClient()
    response = client.get(reverse("country-list"), {"shippable": "true"})

    codes = {row["alpha2"] for row in response.json()["results"]}
    assert country.alpha_2 not in codes


def test_shippable_false_returns_the_complement(as_tenant_host):
    provider = ShippingProviderFactory(code="shippable_false_test")
    rated = CountryFactory()
    unrated = CountryFactory()
    ShippingRateFactory(provider=provider, country=rated)

    client = APIClient()
    # ``alpha_2__in`` scopes the list to just these two rows: a plain
    # unfiltered/paginated list carries every country another test in
    # this worker's DB has created too (real or ad-hoc), and no page
    # size is large enough to guarantee ``unrated`` survives sharing a
    # worker with the rest of this directory under ``-n auto``.
    response = client.get(
        reverse("country-list"),
        {
            "shippable": "false",
            "alpha_2__in": f"{rated.alpha_2},{unrated.alpha_2}",
        },
    )

    codes = {row["alpha2"] for row in response.json()["results"]}
    assert rated.alpha_2 not in codes
    assert unrated.alpha_2 in codes


def test_omitting_the_param_returns_every_country(as_tenant_host):
    provider = ShippingProviderFactory(code="shippable_omit_test")
    rated = CountryFactory()
    unrated = CountryFactory()
    ShippingRateFactory(provider=provider, country=rated)

    client = APIClient()
    response = client.get(
        reverse("country-list"),
        {"alpha_2__in": f"{rated.alpha_2},{unrated.alpha_2}"},
    )

    codes = {row["alpha2"] for row in response.json()["results"]}
    assert rated.alpha_2 in codes
    assert unrated.alpha_2 in codes


def test_shippable_true_on_the_platform_host_returns_none():
    """The platform host has no tenant schema of its own to hold a
    ``ShippingRate`` — ``true`` must never leak one tenant's coverage
    onto it. No ``as_tenant_host`` here: the stripped test settings'
    default ``connection.schema_name`` ("public") already IS the
    platform-host condition this guards.
    """
    provider = ShippingProviderFactory(code="shippable_platform_test")
    country = CountryFactory()
    ShippingRateFactory(provider=provider, country=country)

    client = APIClient()
    response = client.get(reverse("country-list"), {"shippable": "true"})

    assert response.json()["results"] == []


def test_both_kinds_for_the_same_provider_count_as_one_country(
    as_tenant_host,
):
    """A country with rates under two kinds is not double-counted —
    the regression this filter's own ``.distinct()`` call guards
    against (``ShippingRate.Meta.ordering`` interacting with
    ``SELECT DISTINCT``)."""
    provider = ShippingProviderFactory(
        code="shippable_two_kinds_test",
        supports_home_delivery=True,
        supports_pickup_point=True,
    )
    country = CountryFactory()
    ShippingRateFactory(
        provider=provider, country=country, kind=ShippingKind.HOME_DELIVERY
    )
    ShippingRateFactory(
        provider=provider, country=country, kind=ShippingKind.PICKUP_POINT
    )

    client = APIClient()
    response = client.get(reverse("country-list"), {"shippable": "true"})

    matches = [
        row
        for row in response.json()["results"]
        if row["alpha2"] == country.alpha_2
    ]
    assert len(matches) == 1
