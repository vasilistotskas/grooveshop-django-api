"""A zone's region set changes through its M2M, which writes its rows with
``bulk_create`` and sends no ``post_save``: ``shipping.signals`` marks the
surfaces that render where the store delivers."""

from __future__ import annotations

from unittest import mock

import pytest

from core.cache.invalidation import surfaces_invalidated_by
from country.factories import CountryFactory
from region.factories import RegionFactory
from shipping.factories import ShippingZoneFactory
from shipping.models import ShippingProvider

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("change", ["add", "remove", "clear"])
def test_a_region_set_change_marks_the_shipping_surfaces(change):
    country = CountryFactory(num_regions=0)
    region = RegionFactory(country=country)
    zone = ShippingZoneFactory(country=country, regions=[region])

    with mock.patch("shipping.signals.mark_surface_stale") as mark:
        if change == "add":
            zone.regions.add(RegionFactory(country=country))
        elif change == "remove":
            zone.regions.remove(region)
        else:
            zone.regions.clear()

    assert {call.args[0] for call in mark.call_args_list} == {
        "shipping",
        "regions_countries",
    }


def test_a_carrier_switched_on_or_off_marks_the_region_and_country_pickers():
    # Which regions and countries are shippable depends on the carrier's
    # ``is_active`` as much as on its rates.
    assert {"shipping", "regions_countries"} <= set(
        surfaces_invalidated_by(ShippingProvider)
    )
