"""The store admin's Shipping Zones page: a zone is saved with its regions
and rates, and the rules that keep the zones coherent refuse the form
(``shipping.zones.zone_errors`` / ``rate_kind_errors``)."""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from country.factories import CountryFactory
from region.factories import RegionFactory
from shipping.factories import ShippingZoneFactory, whole_country_zone
from shipping.models import ShippingProvider, ShippingZone
from tenant.enum import TenantMembershipRole
from tests.utils.staff import (
    bind_store_tenant,
    store_staff,
    store_tenant,
    unbind_store_tenant,
)

pytestmark = pytest.mark.django_db

STAFF_BACKEND = "tenant.auth_backends.PlatformStaffBackend"
ADD = "admin:shipping_shippingzone_add"


@pytest.fixture
def client():
    tenant = store_tenant("zones_admin")
    previous = bind_store_tenant(tenant)
    client = Client()
    client.force_login(
        store_staff(tenant, role=TenantMembershipRole.ADMIN),
        backend=STAFF_BACKEND,
    )
    yield client
    unbind_store_tenant(previous)


@pytest.fixture
def country():
    return CountryFactory(num_regions=0)


def _post(client, country, *, name="Islands", regions=(), rates=()):
    data = {
        "name": name,
        "country": country.pk,
        "covered_regions": [region.pk for region in regions],
        "rates-TOTAL_FORMS": str(len(rates)),
        "rates-INITIAL_FORMS": "0",
        "rates-MIN_NUM_FORMS": "0",
        "rates-MAX_NUM_FORMS": "1000",
    }
    for index, (provider, kind) in enumerate(rates):
        prefix = f"rates-{index}-"
        data |= {
            f"{prefix}provider": provider.pk,
            f"{prefix}kind": kind,
            f"{prefix}price_0": "7.50",
            f"{prefix}price_1": "EUR",
            f"{prefix}free_shipping_threshold_0": "",
            f"{prefix}free_shipping_threshold_1": "EUR",
            f"{prefix}is_active": "on",
        }
    return client.post(reverse(ADD), data)


def _form_errors(response) -> dict:
    return response.context["adminform"].form.errors


def _rates_errors(response) -> list:
    (rates,) = response.context["inline_admin_formsets"]
    return rates.formset.non_form_errors()


def test_a_region_zone_is_saved_with_its_regions_and_rates(client, country):
    islands = RegionFactory(country=country)
    flat_rate = ShippingProvider.objects.get(code="flat_rate")

    response = _post(
        client,
        country,
        regions=[islands],
        rates=[(flat_rate, "home_delivery")],
    )

    assert response.status_code == 302, response.content
    zone = ShippingZone.objects.get(name="Islands")
    assert list(zone.regions.all()) == [islands]
    assert zone.rates.get().price.amount == 7.5


def test_a_second_whole_country_zone_is_refused(client, country):
    whole_country_zone(country)

    response = _post(client, country, name="Again")

    assert response.status_code == 200
    assert not ShippingZone.objects.filter(name="Again").exists()
    assert "covered_regions" in _form_errors(response)


def test_a_region_another_zone_holds_is_refused(client, country):
    islands = RegionFactory(country=country)
    ShippingZoneFactory(country=country, regions=[islands])

    response = _post(client, country, name="Twice", regions=[islands])

    assert response.status_code == 200
    assert not ShippingZone.objects.filter(name="Twice").exists()
    assert "covered_regions" in _form_errors(response)


def test_a_region_of_another_country_is_refused(client, country):
    elsewhere = RegionFactory(country=CountryFactory(num_regions=0))

    response = _post(client, country, name="Foreign", regions=[elsewhere])

    assert response.status_code == 200
    assert not ShippingZone.objects.filter(name="Foreign").exists()
    assert "covered_regions" in _form_errors(response)


def test_a_pickup_rate_on_a_region_zone_is_refused(client, country):
    islands = RegionFactory(country=country)
    boxnow = ShippingProvider.objects.get(code="boxnow")

    response = _post(
        client,
        country,
        regions=[islands],
        rates=[(boxnow, "pickup_point")],
    )

    assert response.status_code == 200
    assert not ShippingZone.objects.filter(name="Islands").exists()
    assert _rates_errors(response)


def test_a_pickup_rate_on_the_whole_country_zone_is_saved(client, country):
    boxnow = ShippingProvider.objects.get(code="boxnow")

    response = _post(
        client, country, name="Whole", rates=[(boxnow, "pickup_point")]
    )

    assert response.status_code == 302, response.content
    assert ShippingZone.objects.get(name="Whole").rates.count() == 1


@pytest.mark.assert_english
def test_the_changelist_says_what_each_zone_covers(client, country):
    whole_country_zone(country)
    ShippingZoneFactory(
        name="Two regions",
        country=country,
        regions=[RegionFactory(country=country) for _ in "ab"],
    )

    response = client.get(reverse("admin:shipping_shippingzone_changelist"))

    assert response.status_code == 200
    body = response.content.decode()
    assert "Whole country" in body
    assert "2 regions" in body
