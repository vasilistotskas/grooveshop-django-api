"""``ShippingService.shipping_policy`` and the eligibility it shares.

The policy is one row per (provider, kind, country) the store offers,
and "offers" is :meth:`ShippingService.offered_rates`, the filter
``available_options`` runs on: the last test pins that the two agree.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from country.factories import CountryFactory
from shipping.enum import ShippingKind
from shipping.models import ShippingProvider, ShippingRate
from shipping.services import ShippingService
from shipping_acs.models import AcsStation
from tests.utils.shipping import enable_rate

pytestmark = pytest.mark.django_db


@pytest.fixture
def _acs_only(acs_configured_tenant, acs_locker_station):
    """ACS the only active provider, with a cached GR locker."""
    ShippingProvider.objects.filter(code="acs").update(is_active=True)
    ShippingProvider.objects.exclude(code="acs").update(is_active=False)


def _row(policy, *, provider_code, kind, country_code="GR"):
    (row,) = [
        entry
        for entry in policy
        if entry["provider_code"] == provider_code
        and entry["kind"] == kind.value
        and entry["country_code"] == country_code
    ]
    return row


def test_a_store_with_no_active_provider_has_an_empty_policy():
    ShippingProvider.objects.update(is_active=False)

    assert ShippingService.shipping_policy() == []


def test_a_row_carries_exactly_what_the_rate_holds(_acs_only):
    ShippingRate.objects.filter(
        provider__code="acs",
        country_id="GR",
        kind=ShippingKind.HOME_DELIVERY.value,
    ).update(
        price=Decimal("4.20"),
        free_shipping_threshold=Decimal("55.00"),
        delivery_days_min=1,
        delivery_days_max=3,
        max_weight_grams=20000,
    )

    row = _row(
        ShippingService.shipping_policy(),
        provider_code="acs",
        kind=ShippingKind.HOME_DELIVERY,
    )

    assert row == {
        "country_code": "GR",
        "kind": "home_delivery",
        "provider_code": "acs",
        "provider_name": ShippingProvider.objects.get(code="acs").name,
        "price": Decimal("4.20"),
        "currency": "EUR",
        "free_shipping_threshold": Decimal("55.00"),
        "delivery_days_min": 1,
        "delivery_days_max": 3,
        "max_weight_grams": 20000,
    }


def test_unset_optional_rate_fields_are_none(_acs_only):
    ShippingRate.objects.filter(
        provider__code="acs",
        country_id="GR",
        kind=ShippingKind.HOME_DELIVERY.value,
    ).update(
        free_shipping_threshold=None,
        delivery_days_min=None,
        delivery_days_max=None,
        max_weight_grams=None,
    )

    row = _row(
        ShippingService.shipping_policy(),
        provider_code="acs",
        kind=ShippingKind.HOME_DELIVERY,
    )

    assert row["free_shipping_threshold"] is None
    assert row["delivery_days_min"] is None
    assert row["delivery_days_max"] is None
    assert row["max_weight_grams"] is None


def test_an_inactive_rate_is_left_out(_acs_only):
    ShippingRate.objects.filter(
        provider__code="acs", kind=ShippingKind.HOME_DELIVERY.value
    ).update(is_active=False)

    kinds = {row["kind"] for row in ShippingService.shipping_policy()}

    assert kinds == {ShippingKind.PICKUP_POINT.value}


def test_an_inactive_provider_is_left_out(_acs_only):
    ShippingProvider.objects.filter(code="acs").update(is_active=False)

    assert ShippingService.shipping_policy() == []


def test_acs_pickup_needs_stations(_acs_only):
    AcsStation.objects.update(is_active=False)

    kinds = {row["kind"] for row in ShippingService.shipping_policy()}

    assert kinds == {ShippingKind.HOME_DELIVERY.value}


def test_every_shippable_country_is_listed(_acs_only):
    germany = CountryFactory(alpha_2="DE")
    enable_rate(germany, provider_code="acs", price=Decimal("9.90"))

    policy = ShippingService.shipping_policy()

    home = {
        row["country_code"]: row["price"]
        for row in policy
        if row["kind"] == ShippingKind.HOME_DELIVERY.value
    }
    assert set(home) == {"GR", "DE"}
    assert home["DE"] == Decimal("9.90")
    # Germany has no cached locker station, so no pickup row there.
    assert not [
        row
        for row in policy
        if row["country_code"] == "DE"
        and row["kind"] == ShippingKind.PICKUP_POINT.value
    ]


def test_the_policy_is_exactly_what_checkout_offers(_acs_only):
    germany = CountryFactory(alpha_2="DE")
    enable_rate(germany, provider_code="flat_rate", price=Decimal("7.00"))

    offered = set()
    for country_code in ShippingService.shippable_country_codes():
        offered |= {
            (option["provider_code"], option["kind"], country_code)
            for option in ShippingService.available_options(
                country_code=country_code
            )
        }

    assert offered
    assert {
        (row["provider_code"], row["kind"], row["country_code"])
        for row in ShippingService.shipping_policy()
    } == offered
