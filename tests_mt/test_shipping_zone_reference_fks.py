"""``ShippingZone`` (TENANT_APPS) referencing ``country.Country`` and
``region.Region`` (SHARED_APPS) — the same cross-schema FK shape
``Order.country`` already uses, exercised under the REAL tenant router
the main suite strips.

Covers:
* The zone's country and its region set resolve under ``schema_context``
  (a tenant schema has no ``country_country`` or ``region_region`` of
  its own — the FKs only work because the tenant's search_path falls
  through to public for them), and a rate is reached through its zone.
* A store's schema replayed from scratch (provisioning) puts the rates
  its migrations seed in one zone covering the whole country.
* ``shipping_shippingzone`` itself is schema-local: absent from public.
* Two tenants can each put the SAME shared region in a zone of their
  own: ``ShippingZoneRegion``'s unique region holds per store.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import transaction
from django.db.utils import ProgrammingError
from django_tenants.utils import schema_context


def _gr_and_region(alpha: str):
    """GR (seeded once at schema creation, so idempotent here) and one
    region of it, written in public, where reference data lives."""
    from country.models import Country
    from region.models import Region

    with schema_context("public"):
        country, _ = Country.objects.get_or_create(
            alpha_2="GR",
            defaults={"alpha_3": "GRC", "phone_code": 30, "sort_order": 0},
        )
        region, _ = Region.objects.get_or_create(
            alpha=alpha, country=country, defaults={"sort_order": 0}
        )
    return country, region


@pytest.mark.django_db
def test_a_zone_resolves_its_cross_schema_country_and_regions(mt_tenant):
    from shipping.factories import ShippingProviderFactory, ShippingZoneFactory
    from shipping.models import ShippingRate, ShippingZone

    country, region = _gr_and_region("ZMT1")

    with schema_context(mt_tenant.schema_name):
        zone = ShippingZoneFactory(country=country, regions=[region])
        rate = ShippingRate.objects.create(
            provider=ShippingProviderFactory(code="mt_zone_probe"),
            zone=zone,
            kind="home_delivery",
            price=Decimal("3.00"),
        )
        fetched = ShippingZone.objects.select_related("country").get(pk=zone.pk)
        assert fetched.country.alpha_2 == "GR"
        assert list(fetched.regions.values_list("alpha", flat=True)) == ["ZMT1"]
        assert ShippingRate.objects.get(pk=rate.pk).zone.country_id == "GR"


@pytest.mark.django_db
def test_a_provisioned_store_has_its_seeded_rates_in_a_whole_country_zone(
    mt_tenant,
):
    """Migrations ``0023``/``0024`` ran in the store's schema when it was
    created: every rate ``0011`` seeded is in GR's whole-country zone."""
    from shipping.models import ShippingRate

    with schema_context(mt_tenant.schema_name):
        rates = ShippingRate.objects.select_related("zone")
        assert rates.exists()
        assert {rate.zone.country_id for rate in rates} == {"GR"}
        assert all(not rate.zone.regions.exists() for rate in rates)


@pytest.mark.django_db
def test_zone_rows_are_schema_local(mt_tenant):
    """``shipping_shippingzone`` is TENANT_APPS-only — the table must not
    exist in the public schema AT ALL."""
    from shipping.factories import ShippingZoneFactory
    from shipping.models import ShippingZone

    country, _region = _gr_and_region("ZMT2")
    with schema_context(mt_tenant.schema_name):
        zone_id = ShippingZoneFactory(country=country).pk

    with schema_context("public"):
        with (
            pytest.raises(ProgrammingError, match="does not exist"),
            transaction.atomic(),
        ):
            ShippingZone.objects.filter(pk=zone_id).exists()


@pytest.mark.django_db
def test_two_tenants_can_zone_the_same_region_independently(
    mt_tenant, mt_tenant_b
):
    """Region stays global, each store decides where it delivers: the
    unique region of ``ShippingZoneRegion`` is per schema."""
    from shipping.factories import ShippingZoneFactory
    from shipping.models import ShippingZone

    country, region = _gr_and_region("ZMT3")

    with schema_context(mt_tenant.schema_name):
        ShippingZoneFactory(name="A islands", country=country, regions=[region])
    with schema_context(mt_tenant_b.schema_name):
        ShippingZoneFactory(name="B islands", country=country, regions=[region])

    with schema_context(mt_tenant.schema_name):
        assert ShippingZone.objects.get(regions=region).name == "A islands"
    with schema_context(mt_tenant_b.schema_name):
        assert ShippingZone.objects.get(regions=region).name == "B islands"
